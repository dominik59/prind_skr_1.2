import bisect
import json

from . import manual_probe


class MeshPlane:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.name = config.get_name()

        # Global planar correction.
        self.x_adjust = config.getfloat(
            "x_adjust", 0.
        )

        self.y_adjust = config.getfloat(
            "y_adjust", 0.
        )

        # The old options are read for compatibility, but ignored.
        config.get("z_adjust", None)
        config.get("x_reference", None)
        config.get("y_reference", None)
        config.get("calibration_points", None)

        # Residual correction grid limits.
        # These are nozzle coordinates.
        self.residual_min = config.getfloatlist(
            "residual_min",
            count=2
        )

        self.residual_max = config.getfloatlist(
            "residual_max",
            count=2
        )

        self.residual_x_count = config.getint(
            "residual_x_count",
            3,
            minval=2
        )

        self.residual_y_count = config.getint(
            "residual_y_count",
            3,
            minval=2
        )

        # Calibration settings.
        legacy_start_z = config.getfloat(
            "calibration_start_z",
            2.0
        )

        legacy_travel_z = config.getfloat(
            "calibration_travel_z",
            5.0
        )

        legacy_travel_speed = config.getfloat(
            "calibration_travel_speed",
            50.0
        )

        legacy_z_speed = config.getfloat(
            "calibration_z_speed",
            5.0
        )

        self.residual_start_z = config.getfloat(
            "residual_start_z",
            legacy_start_z,
            above=0.
        )

        self.residual_travel_z = config.getfloat(
            "residual_travel_z",
            legacy_travel_z,
            above=0.
        )

        self.residual_travel_speed = config.getfloat(
            "residual_travel_speed",
            legacy_travel_speed,
            above=0.
        )

        self.residual_z_speed = config.getfloat(
            "residual_z_speed",
            legacy_z_speed,
            above=0.
        )

        self.manual_probe_speed = config.getfloat(
            "manual_probe_speed",
            5.0,
            above=0.
        )

        self.residual_x_values = self._make_axis_values(
            self.residual_min[0],
            self.residual_max[0],
            self.residual_x_count
        )

        self.residual_y_values = self._make_axis_values(
            self.residual_min[1],
            self.residual_max[1],
            self.residual_y_count
        )

        self.residual_grid = self._load_residual_grid(
            config
        )

        # Existing Klipper transform.
        self.next_transform = None

        # Calibration state.
        self.calibration_active = False
        self.calibration_index = 0
        self.calibration_points = []
        self.calibration_deltas = []
        self.calibration_start_physical_z = None

        self.printer.register_event_handler(
            "klippy:connect",
            self._handle_connect
        )

        self.gcode = self.printer.lookup_object(
            "gcode"
        )

        self.gcode.register_command(
            "SET_MESH_PLANE",
            self.cmd_SET_MESH_PLANE,
            desc="Set planar correction"
        )

        self.gcode.register_command(
            "GET_MESH_PLANE",
            self.cmd_GET_MESH_PLANE,
            desc="Show planar correction"
        )

        self.gcode.register_command(
            "MESH_RESIDUAL_OUTPUT",
            self.cmd_MESH_RESIDUAL_OUTPUT,
            desc="Show residual correction grid"
        )

        self.gcode.register_command(
            "MESH_RESIDUAL_CLEAR",
            self.cmd_MESH_RESIDUAL_CLEAR,
            desc="Clear residual correction grid"
        )

        self.gcode.register_command(
            "MESH_RESIDUAL_CALIBRATE",
            self.cmd_MESH_RESIDUAL_CALIBRATE,
            desc="Calibrate plane and residual grid"
        )

        # Backward-compatible alias.
        self.gcode.register_command(
            "MESH_PLANE_CALIBRATE",
            self.cmd_MESH_RESIDUAL_CALIBRATE,
            desc="Calibrate plane and residual grid"
        )

    # --------------------------------------------------------------
    # Grid helpers
    # --------------------------------------------------------------

    def _make_axis_values(self, minimum, maximum, count):
        if count == 1:
            return [minimum]

        step = (
            maximum - minimum
        ) / float(count - 1)

        return [
            minimum + step * index
            for index in range(count)
        ]

    def _zero_grid(self):
        return [
            [
                0.
                for _ in range(self.residual_x_count)
            ]
            for _ in range(self.residual_y_count)
        ]

    def _load_residual_grid(self, config):
        raw_grid = config.get(
            "residual_grid",
            ""
        ).strip()

        if not raw_grid:
            return self._zero_grid()

        try:
            grid = json.loads(raw_grid)
        except Exception as exc:
            raise config.error(
                "Unable to parse residual_grid: %s"
                % exc
            )

        if len(grid) != self.residual_y_count:
            raise config.error(
                "residual_grid has invalid Y size"
            )

        for row in grid:
            if len(row) != self.residual_x_count:
                raise config.error(
                    "residual_grid has invalid X size"
                )

        return [
            [
                float(value)
                for value in row
            ]
            for row in grid
        ]

    def _persist_residual_grid(self):
        configfile = self.printer.lookup_object(
            "configfile"
        )

        configfile.set(
            self.name,
            "residual_grid",
            json.dumps(
                self.residual_grid,
                separators=(",", ":")
            )
        )

    def _make_calibration_points(self):
        points = []

        for y in self.residual_y_values:
            for x in self.residual_x_values:
                points.append((x, y))

        return points

    # --------------------------------------------------------------
    # Movement transform
    # --------------------------------------------------------------

    def _plane_correction(self, x, y):
        # The reference is automatically the center of the grid.
        x_reference = (
            self.residual_min[0]
            + self.residual_max[0]
        ) / 2.

        y_reference = (
            self.residual_min[1]
            + self.residual_max[1]
        ) / 2.

        return (
            (x - x_reference) * self.x_adjust
            + (y - y_reference) * self.y_adjust
        )

    def _bracket(self, value, axis_values):
        if value <= axis_values[0]:
            return 0, 0, 0.

        last_index = len(axis_values) - 1

        if value >= axis_values[last_index]:
            return last_index, last_index, 0.

        upper = bisect.bisect_right(
            axis_values,
            value
        )

        lower = upper - 1

        distance = (
            axis_values[upper]
            - axis_values[lower]
        )

        if distance <= 0.:
            fraction = 0.
        else:
            fraction = (
                value - axis_values[lower]
            ) / distance

        return lower, upper, fraction

    def _residual_correction(self, x, y):
        x0, x1, tx = self._bracket(
            x,
            self.residual_x_values
        )

        y0, y1, ty = self._bracket(
            y,
            self.residual_y_values
        )

        z00 = self.residual_grid[y0][x0]
        z10 = self.residual_grid[y0][x1]
        z01 = self.residual_grid[y1][x0]
        z11 = self.residual_grid[y1][x1]

        top = z00 + (z10 - z00) * tx
        bottom = z01 + (z11 - z01) * tx

        return top + (bottom - top) * ty

    def _total_correction(self, x, y):
        return (
            self._plane_correction(x, y)
            + self._residual_correction(x, y)
        )

    def _handle_connect(self):
        gcode_move = self.printer.lookup_object(
            "gcode_move"
        )

        # Wrap the existing transform, normally bed_mesh.
        self.next_transform = gcode_move.set_move_transform(
            self,
            force=True
        )

    def move(self, newpos, speed):
        corrected_pos = list(newpos)

        x = corrected_pos[0]
        y = corrected_pos[1]

        corrected_pos[2] += self._total_correction(
            x,
            y
        )

        self.next_transform.move(
            corrected_pos,
            speed
        )

    def get_position(self):
        pos = list(
            self.next_transform.get_position()
        )

        pos[2] -= self._total_correction(
            pos[0],
            pos[1]
        )

        return pos

    # --------------------------------------------------------------
    # Planar correction commands
    # --------------------------------------------------------------

    def _update_plane(
            self,
            x_adjust,
            y_adjust,
            persist=False):

        self.x_adjust = x_adjust
        self.y_adjust = y_adjust

        if persist:
            configfile = self.printer.lookup_object(
                "configfile"
            )

            configfile.set(
                self.name,
                "x_adjust",
                "%.6f" % x_adjust
            )

            configfile.set(
                self.name,
                "y_adjust",
                "%.6f" % y_adjust
            )

        gcode_move = self.printer.lookup_object(
            "gcode_move"
        )

        gcode_move.reset_last_position()

    def cmd_SET_MESH_PLANE(self, gcmd):
        x_adjust = gcmd.get_float(
            "X_ADJUST",
            self.x_adjust
        )

        y_adjust = gcmd.get_float(
            "Y_ADJUST",
            self.y_adjust
        )

        self._update_plane(
            x_adjust,
            y_adjust,
            persist=gcmd.get_int("SAVE", 0)
        )

        gcmd.respond_info(
            "mesh plane: X=%.6f Y=%.6f"
            % (
                self.x_adjust,
                self.y_adjust
            )
        )

    def cmd_GET_MESH_PLANE(self, gcmd):
        gcmd.respond_info(
            "mesh plane: X=%.6f Y=%.6f"
            % (
                self.x_adjust,
                self.y_adjust
            )
        )

    # --------------------------------------------------------------
    # Residual grid commands
    # --------------------------------------------------------------

    def _mesh_is_loaded(self):
        bed_mesh = self.printer.lookup_object(
            "bed_mesh",
            None
        )

        if bed_mesh is None:
            return False

        status = bed_mesh.get_status(None)

        return bool(
            status.get("profile_name")
        )

    def _report_grid(self, respond):
        respond(
            "Residual grid: %dx%d"
            % (
                self.residual_x_count,
                self.residual_y_count
            )
        )

        for y_index, y in enumerate(
                self.residual_y_values):

            respond(
                "Y=%.3f: %s"
                % (
                    y,
                    " ".join(
                        "%.5f" % value
                        for value in self.residual_grid[
                            y_index
                        ]
                    )
                )
            )

    def cmd_MESH_RESIDUAL_OUTPUT(self, gcmd):
        self._report_grid(
            gcmd.respond_info
        )

    def cmd_MESH_RESIDUAL_CLEAR(self, gcmd):
        self.residual_grid = self._zero_grid()

        if gcmd.get_int("SAVE", 0):
            self._persist_residual_grid()

        gcode_move = self.printer.lookup_object(
            "gcode_move"
        )

        gcode_move.reset_last_position()

        gcmd.respond_info(
            "Residual grid cleared"
        )

    def cmd_MESH_RESIDUAL_CALIBRATE(self, gcmd):
        if self.calibration_active:
            raise gcmd.error(
                "Residual calibration is already running"
            )

        if not self._mesh_is_loaded():
            raise gcmd.error(
                "Load a bed mesh before calibration"
            )

        manual_probe.verify_no_manual_probe(
            self.printer
        )

        self.calibration_active = True
        self.calibration_index = 0
        self.calibration_points = \
            self._make_calibration_points()
        self.calibration_deltas = []

        self.gcode.run_script_from_command(
            "SAVE_GCODE_STATE "
            "NAME=mesh_plane_calibration"
        )

        self.gcode.respond_info(
            "Starting combined plane and residual calibration.\n"
            "Use TESTZ to adjust the nozzle, then ACCEPT."
        )

        self._start_calibration_point()

    def _start_calibration_point(self):
        x, y = self.calibration_points[
            self.calibration_index
        ]

        travel_f = self.residual_travel_speed * 60.
        z_f = self.residual_z_speed * 60.

        script = (
            "G90\n"
            "G1 Z%.3f F%.1f\n"
            "G1 X%.3f Y%.3f F%.1f\n"
            "G1 Z%.3f F%.1f"
            % (
                self.residual_travel_z,
                z_f,
                x,
                y,
                travel_f,
                self.residual_start_z,
                z_f
            )
        )

        try:
            self.gcode.run_script_from_command(
                script
            )

            toolhead = self.printer.lookup_object(
                "toolhead"
            )

            toolhead.get_last_move_time()

        except self.printer.command_error:
            self._finish_calibration(False)
            raise

        toolhead = self.printer.lookup_object(
            "toolhead"
        )

        self.calibration_start_physical_z = \
            toolhead.get_position()[2]

        self.gcode.respond_info(
            "Point %d/%d at X=%.2f Y=%.2f"
            % (
                self.calibration_index + 1,
                len(self.calibration_points),
                x,
                y
            )
        )

        manual_gcmd = self.gcode.create_gcode_command(
            "MESH_RESIDUAL_CALIBRATE",
            "MESH_RESIDUAL_CALIBRATE",
            {
                "SPEED": "%.3f"
                % self.manual_probe_speed
            }
        )

        manual_probe.ManualProbeHelper(
            self.printer,
            manual_gcmd,
            self._manual_probe_finished
        )

    def _manual_probe_finished(self, mpresult):
        if mpresult is None:
            self._finish_calibration(False)
            return

        delta_z = (
            mpresult.bed_z
            - self.calibration_start_physical_z
        )

        self.calibration_deltas.append(
            delta_z
        )

        self.gcode.respond_info(
            "Measured correction: %.4f mm"
            % delta_z
        )

        self.calibration_index += 1

        if self.calibration_index >= len(
                self.calibration_points):

            self._finish_calibration(True)
        else:
            self._start_calibration_point()

    def _calculate_plane_and_residual(self):
        x_count = self.residual_x_count
        y_count = self.residual_y_count

        center_x_index = x_count // 2
        center_y_index = y_count // 2

        center_index = (
            center_y_index * x_count
            + center_x_index
        )

        center_delta = self.calibration_deltas[
            center_index
        ]

        # Convert measurements to relative corrections.
        relative = []

        for delta in self.calibration_deltas:
            relative.append(
                delta - center_delta
            )

        x_reference = self.residual_x_values[
            center_x_index
        ]

        y_reference = self.residual_y_values[
            center_y_index
        ]

        # Fit a plane through the center point:
        #
        # correction = bx * dx + by * dy
        #
        # The common Z component is deliberately excluded.
        sxx = 0.
        sxy = 0.
        syy = 0.
        sxd = 0.
        syd = 0.

        for index, (x, y) in enumerate(
                self.calibration_points):

            dx = x - x_reference
            dy = y - y_reference
            correction = relative[index]

            sxx += dx * dx
            sxy += dx * dy
            syy += dy * dy

            sxd += dx * correction
            syd += dy * correction

        determinant = (
            sxx * syy
            - sxy * sxy
        )

        if abs(determinant) < 1e-12:
            raise self.gcode.error(
                "Unable to calculate planar correction"
            )

        plane_x = (
            sxd * syy
            - syd * sxy
        ) / determinant

        plane_y = (
            sxx * syd
            - sxy * sxd
        ) / determinant

        residual_delta_grid = []

        for y_index in range(y_count):
            row = []

            for x_index in range(x_count):
                index = (
                    y_index * x_count
                    + x_index
                )

                x = self.residual_x_values[
                    x_index
                ]

                y = self.residual_y_values[
                    y_index
                ]

                dx = x - x_reference
                dy = y - y_reference

                plane_value = (
                    plane_x * dx
                    + plane_y * dy
                )

                residual_value = (
                    relative[index]
                    - plane_value
                )

                row.append(residual_value)

            residual_delta_grid.append(row)

        return (
            plane_x,
            plane_y,
            residual_delta_grid
        )

    def _finish_calibration(self, success):
        self.calibration_active = False

        if not success:
            self.gcode.run_script_from_command(
                "RESTORE_GCODE_STATE "
                "NAME=mesh_plane_calibration "
                "MOVE=1"
            )

            self.gcode.respond_info(
                "Calibration aborted"
            )

            return

        plane_x, plane_y, residual_delta = \
            self._calculate_plane_and_residual()

        # Add the newly measured planar component.
        self.x_adjust += plane_x
        self.y_adjust += plane_y

        # Add the local residual component.
        for y_index in range(
                self.residual_y_count):

            for x_index in range(
                    self.residual_x_count):

                self.residual_grid[
                    y_index
                ][x_index] += residual_delta[
                    y_index
                ][x_index]

        configfile = self.printer.lookup_object(
            "configfile"
        )

        configfile.set(
            self.name,
            "x_adjust",
            "%.6f" % self.x_adjust
        )

        configfile.set(
            self.name,
            "y_adjust",
            "%.6f" % self.y_adjust
        )

        self._persist_residual_grid()

        gcode_move = self.printer.lookup_object(
            "gcode_move"
        )

        gcode_move.reset_last_position()

        self.gcode.run_script_from_command(
            "RESTORE_GCODE_STATE "
            "NAME=mesh_plane_calibration "
            "MOVE=1"
        )

        self.gcode.respond_info(
            "Combined calibration complete.\n"
            "Planar increment: X=%.6f Y=%.6f\n"
            "New planar values: X=%.6f Y=%.6f\n"
            "Common Z value was ignored.\n"
            "Residual grid updated.\n"
            "Run SAVE_CONFIG to make it permanent."
            % (
                plane_x,
                plane_y,
                self.x_adjust,
                self.y_adjust
            )
        )

        self._report_grid(
            self.gcode.respond_info
        )


def load_config(config):
    return MeshPlane(config)
