"""Guided tour and orbit motion of `SpaceCanvasView` (`TourMotionMixin`)."""

from __future__ import annotations



class TourMotionMixin:
    """Guided tour steps, orbit motion toggle and the animation tick.

    Plain mixin (no Qt base) used only by `SpaceCanvasView`; see `ui.canvas.view`.
    """

    def set_orbit_motion(self, enabled: bool) -> None:
        self.orbit_motion_enabled = bool(enabled)
        self.timer.setInterval(self.MOTION_TICK_MS if self.orbit_motion_enabled else self.IDLE_TICK_MS)

    def _on_animation_tick(self) -> None:
        if self.view_mode == "system" and self.orbit_motion_enabled:
            for p_item in self.planet_items:
                p_item.advance_orbit(1.0)
            for l_item in self.link_items:
                l_item.update_geometry()

    # ------------------------------------------------------------------
    # Guided tour
    # ------------------------------------------------------------------
    def start_or_advance_tour(self) -> None:
        if not self.active_system:
            return
        if self.view_mode != "system":
            self.enter_solar_system(self.active_system)

        steps = self.active_system.tour_steps
        if not steps:
            self.active_system.rebuild_links_and_tour()
            steps = self.active_system.tour_steps
        hidden = self.map_filter.hidden_planet_ids(self.active_system) if self.map_filter.is_active() else set()
        steps = [pid for pid in steps if pid not in hidden]

        total = len(steps) + 1
        self.tour_index = (self.tour_index + 1) % total

        for p_item in self.planet_items:
            p_item.is_tour_active = False
            p_item.update()

        if self.tour_index == 0:
            self.fit_to_view()
            self.select_object(self.active_system, "sun", self.sun_item)
            self.tourStepChanged.emit(1, total, self.active_system.project_name or self.active_system.name)
        else:
            planet_id = steps[self.tour_index - 1]
            p_item = self.planet_item_map.get(planet_id)
            if p_item:
                p_item.is_tour_active = True
                p_item.update()
                self._auto_fit = False
                self.resetTransform()
                self.scale(1.1, 1.1)
                self._current_scale = 1.1
                self.centerOn(p_item.scenePos())
                self.select_object(p_item.planet, "planet", p_item)
                self.tourStepChanged.emit(self.tour_index + 1, total, p_item.planet.title)

    def stop_tour(self) -> None:
        self.tour_index = -1
        for p_item in self.planet_items:
            p_item.is_tour_active = False
            p_item.update()
