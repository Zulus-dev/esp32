# hardware/buzzer.py
import asyncio

from machine import Pin, PWM
from config import Config


class Buzzer:
    def __init__(self):
        self.pin = Pin(Config.BUZZER_PIN, Pin.OUT)
        self._pwm = None
        self.pin.off()

    async def boot_test(self):
        """Audible startup self-test for the passive buzzer."""
        await self.beep(Config.BUZZER_BOOT_1_HZ, 70)
        await asyncio.sleep_ms(40)
        await self.beep(Config.BUZZER_BOOT_2_HZ, 90)

    async def beep(self, freq=None, duration=40):
        pwm = None
        try:
            pwm = PWM(self.pin, freq=freq or Config.BUZZER_CLICK_HZ)
            self._pwm = pwm
            self._set_duty(pwm, Config.BUZZER_DUTY_U16)
            await asyncio.sleep_ms(duration)
            self._set_duty(pwm, 0)
        except Exception as exc:
            print("[BUZZER]", exc)
        finally:
            try:
                if pwm:
                    pwm.deinit()
            except Exception:
                pass
            self._pwm = None
            try:
                self.pin.off()
            except Exception:
                pass

    def _set_duty(self, pwm, duty_u16):
        try:
            pwm.duty_u16(duty_u16)
        except AttributeError:
            pwm.duty((duty_u16 * 1023) // 65535)
