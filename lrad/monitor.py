import logging
import re
import subprocess

log = logging.getLogger(__name__)


class LRADMonitor:
    # TODO: Make this a 1dp set based on the curve
    COMPARISONS = {
        10:     694.0,
        10.5:   730.0,
        11:     768.0,
        11.5:   812.0,
        12:     850.0,
        12.5:   888.0,
        13:     926.0,
        99:     1500.0          # Test voltage to trigger in place (don't judge me)
    }
    CRITICAL = "_critical"
    WARNING = "_warning"

    def __init__(self,
                 critical,
                 warning):

        if warning not in LRADMonitor.COMPARISONS.keys() and critical not in LRADMonitor.COMPARISONS.keys():
                raise InvalidMonitorVoltage

        self._critical = critical
        self._warning = warning
        self._current_battery_reading = None

    def compare_battery_voltage(self, compare = WARNING):
        """
        Returns true if voltage is OK, false if we've dropped below comparison value
        """
        adc_info = subprocess.check_output(["tshwctl", "-V"], universal_newlines=True).split('\n')
        voltage_info = re.match(r'LRADC_ADC2_millivolts=(\d+)', adc_info[1])

        if voltage_info:
            self._current_battery_reading = float(voltage_info.group(1))
            log.info("Comparing {0} to {1}V equivalent {2}".format(
                self._current_battery_reading, getattr(self, compare), LRADMonitor.COMPARISONS[getattr(self, compare)]))

            if self._current_battery_reading < LRADMonitor.COMPARISONS[getattr(self, compare)]:
                return True
        return False

    def get_battery_reading(self):
        return self._current_battery_reading

    def set_critical_voltage(self, voltage):
        self._critical = voltage

    def set_warning_voltage(self, voltage):
        self._warning = voltage


class InvalidMonitorVoltage(BaseException):
    pass