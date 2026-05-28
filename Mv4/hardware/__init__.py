# hardware package intentionally performs no eager driver imports.
# Import concrete drivers from their modules, e.g.:
#   from hardware.oled import OLED
# This keeps OLED boot independent from buttons/buzzer async dependencies.
