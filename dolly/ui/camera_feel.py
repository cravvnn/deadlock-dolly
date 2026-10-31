"""Camera feel on the Controls page: the mouse sensitivity presets, slider and field built with the page kit.

Extends ``settings_page.CameraFeel`` (whose legacy layout stays for its own callers and tests) with a
kit card layout (PLAN-5 3.4).
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import settings_page
from .kit_controls import KitSlider
from .settings_page import SENSITIVITY_PRESETS, SENSITIVITY_TICKS, sensitivity_to_position

# The legacy card's description, now the Camera Feel title's info bubble (same copy).
FEEL_INFO = ("Free-camera look speed. Lower it when a high-DPI mouse makes the camera spin, "
             "or raise it for a slow desktop mouse.")


class CameraFeel(settings_page.CameraFeel):
    """Camera look preferences: five presets, a log-scale slider and the exact value.

    Without ``card`` this is the legacy card (``settings_page.CameraFeel``, unchanged). With
    ``card`` (a :class:`~dolly.ui.kit_card.KitCard`, Figma [design reference]) it fills that card: the
    presets wrap as hug-width canvas buttons, the slider is the kit slider, and the
    inherited handlers keep the behaviour (live text while moving, apply and save on
    release, presets apply and save, the field's ``<Return>`` saves).
    """

    def __init__(self, body, mouse_sensitivity, save_sensitivity, *, card=None):
        if card is None:
            super().__init__(body, mouse_sensitivity, save_sensitivity)
            return
        self.card = card
        self.mouse_sensitivity = mouse_sensitivity
        self.save_sensitivity = save_sensitivity
        self._sensitivity_syncing = False
        host = card.host
        self.sensitivity_position = tk.DoubleVar(host, value=sensitivity_to_position(self._sensitivity_value()))
        self.sensitivity_text = tk.StringVar(host)
        presets = card.flow()
        self.preset_buttons = [presets.button(label, lambda preset=value: self._apply_sensitivity(preset))
                               for label, value in SENSITIVITY_PRESETS]
        row = card.row(height=12, top=15)
        self.slider = row.widget(KitSlider(row.host, from_=0, to=SENSITIVITY_TICKS,
                                           variable=self.sensitivity_position, command=self._sensitivity_moved),
                                 width="fill")
        self.slider.bind("<ButtonRelease-1>", self._sensitivity_released, add="+")
        self.sensitivity_label = card.text(textvariable=self.sensitivity_text, top=8)
        row = card.row(top=15)
        row.label("Mouse sensitivity", width=105)
        row.gap(10)
        self.sensitivity_entry = row.widget(ttk.Entry(row.host, textvariable=mouse_sensitivity, style="Kit.TEntry"),
                                            width="fill")
        self.sensitivity_entry.bind("<Return>", lambda _event: self.save_sensitivity())
        self.save_button = card.row().button("Save sensitivity", save_sensitivity)
        self.mouse_sensitivity.trace_add("write", self._sensitivity_state_changed)
        self._sensitivity_text_refresh(self._sensitivity_value())
