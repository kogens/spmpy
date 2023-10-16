from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

import pint
from pint import UnitRegistry, Quantity

RE_CIAO_PARAM = re.compile(
    r'^\\?@?(?:(?P<group>\d?):)?(?P<param>.*): (?P<type>\w)\s?(?:\[(?P<softscale>.*)\])?\s?(?:\((?P<hardscale>.*)\))?\s(?P<hardval>.*)$')

# Define regex to identify numerical values
RE_NUMERICAL = re.compile(r'^([+-]?\d+\.?\d*(?:[eE][+-]\d+)?)( 1?[^\d:]+)?$')
RE_MULTIPLE_NUMERICAL = re.compile(r'^((?:(?<!\d)[+-]?\d+\.?\d*(?:e[+-]\d+)? ?)+)([^\d:]+)?$')

INTEGER_SIZE = 2 ** 16  # 16-bit ADC according to manual

# Pint UnitRegistry handles physical quantities
ureg = UnitRegistry()
ureg.define(f'least_significant_bit  = {INTEGER_SIZE} = LSB')
ureg.define('arbitrary_units = [] = Arb')
ureg.define('log_arbitrary_units = [] = log_Arb')
ureg.define('log_volt = [] = log_V')
ureg.define('log_pascal = [] = log_Pa')
ureg.define('@alias degree = º')  # The files use "Ordinal indicator": º instead of the actual degree symbol: °
ureg.default_format = '~C'

"""
https://masteringelectronicsdesign.com/an-adc-and-dac-least-significant-bit-lsb/
"What is an LSB? The LSB is the smallest level that an ADC can convert, or is the smallest increment 
a DAC outputs."

LSB = V_ref / 2^N = "hard scale"
    v
V_ref/LSB = 2^N = counts = "number of bits"
    v
V_ref = 2^N * LSB, sometimes "hard value"

LSB is the "voltage reoslution" of the ADC/DAC and represents the smallest 

"""


@dataclass
class CIAOParameter:
    r"""
    In the file header some parameters start with '\@' instead of simply '\'. This is an indication to the software
    that the data that follows is intended for a CIAO parameter object. After the '@', you might see a number
    followed by a colon before the label. This number is what we call a “group number” and can generally be
    ignored.

    Further, after the label and its colon, you will see a single definition character of 'V', 'C', or 'S'.
    - V means Value – a parameter that contains a double and a unit of measure, and some scaling
    definitions.
    - C means Scale – a parameter that is simply a scaled version of another.
    - S means Select – a parameter that describes some selection that has been made.
    """

    name: str  # Parameter name

    def __str__(self):
        return f'{self.name}: ' + (f'"{self.value}"' if isinstance(self.value, str) else f'{self.value}')

    @property
    def value(self):
        return None

    @classmethod
    def from_string(cls, ciao_string):
        match = RE_CIAO_PARAM.match(ciao_string)
        if match:
            group = int(match.group('group')) if match.group('group') else None
            name = match.group('param')
            parameter_type = match.group('type')
            soft_scale = parse_parameter_value(match.group('softscale'))
            hard_scale = parse_parameter_value(match.group('hardscale')) if match.group('hardscale') else None
            value = parse_parameter_value(match.group('hardval')) if match.group('hardval') else None

            if parameter_type == 'V':
                return ValueParameter(group=group,
                                      name=name,
                                      soft_scale=soft_scale,
                                      hard_scale=hard_scale,
                                      hard_value=value)
            elif parameter_type == 'S':
                return SelectParameter(group=group,
                                       name=name,
                                       internal_designation=soft_scale,
                                       external_designation=value)
            elif parameter_type == 'C':
                return ScaleParameter(group=group,
                                      name=name,
                                      soft_scale=soft_scale,
                                      hard_value=value)
            else:
                raise ValueError(f'Not a recognized CIAO parameter type: {parameter_type}. Allowed types: V, S, C')
        else:
            raise ValueError(f'Not a recognized CIAO parameter object: {ciao_string}')


@dataclass
class ValueParameter(CIAOParameter):
    """
    The Value (identified by the letter “V”) parameters have the following format:
        [soft-scale] (hard-scale) hard-value.

    A value parameter might be missing a soft-scale or a hard-scale, but must always have a hard-value.

    The hard scale is the conversion factor we use to convert LSBs into hard values.

    The hard-value is the value you would read with a voltmeter inside of
    the NanoScope electronics or inside the head. This value is always in volts with the exception of the Drive
    Frequency (which is in Hertz) and some STM parameters (which are in Amps).

    A soft-value is what the user sees on the screen when the Units: are set to Metric.

    The soft-scale is what we use to convert a hard-value into a soft-value. Soft-scales are user defined, or are
    calibration numbers that the user divines. Soft-scales in the parameters are typically not written out —
    rather, another tag appears between the brackets, like [Sens. Zsens]. In that case, you look elsewhere in the
    parameter list for tag and use that parameter's hard-value for the soft-scale.
    """
    name: str
    hard_value: float | str | Quantity

    group: int = None
    hard_scale: float | Quantity = None
    soft_scale: str = None  # Usually refers to another parameter in the file
    soft_scale_value: float | Quantity = None  # Actual value of the soft scale

    @property
    def ptype(self):
        return 'V'

    @property
    def value(self):
        return self.hard_value

    @property
    def ciao_string(self):
        group_string = f'{self.group}:' if self.group else ''
        hscale_string = f' ({self.hard_scale})' if self.hard_scale else ''
        sscale_string = f' [{self.soft_scale}]' if self.soft_scale else ''

        return f'\\@{group_string}{self.name}: {self.ptype}{sscale_string}{hscale_string} {self.hard_value}'


@dataclass
class SelectParameter(CIAOParameter):
    """
    The Select parameters (identified by the letter “S”) have the following format:
        [Internal-designation for selection] “external-designation for selection”
    """
    name: str
    internal_designation: str
    external_designation: str
    group: int = None

    @property
    def ptype(self):
        return 'S'

    @property
    def value(self):
        return self.external_designation

    @property
    def ciao_string(self):
        group_string = f'{self.group}:' if self.group else ''
        return f'\\@{group_string}{self.name}: {self.ptype} [{self.internal_designation}] "{self.external_designation}"'


@dataclass
class ScaleParameter(CIAOParameter):
    """
    The Scale parameters (identified by the letter “C”) have the following format:
        [soft-scale] hard-value.
    The hard-value is almost always a scalar value.
    The soft-scale always points to another parameter – this parameter is the target of the scaling
    action.
    Most often used for the Z magnify parm to allow user to change scaling of Z scale in Off-
    line without actually affecting the real data in the file.
    """
    name: str
    soft_scale: str
    hard_value: str
    group: int = None

    @property
    def ptype(self):
        return 'C'

    @property
    def value(self):
        return self.hard_value

    @property
    def ciao_string(self):
        group_string = f'{self.group}:' if self.group else ''
        return f'\\@{group_string} {self.name}: {self.ptype} [{self.soft_scale}] {self.hard_value}'


def parse_parameter_value(value_str: str) -> str | int | float | Quantity | datetime | list[float, Quantity] | None:
    """ Parse parameters into number, string or physical quantity """

    # Value is None, return it
    if not value_str:
        return value_str

    # Strip whitespace and check for matches
    value_str = value_str.strip()
    match_numerical = RE_NUMERICAL.match(value_str)
    if match_numerical and match_numerical.group(2):
        # Value  is a quantity with unit
        unit = match_numerical.group(2)
        # A few units appear as e.g. log(Pa), replace with log_Pa etc.
        if '(' in unit:
            unit = unit.replace('(', '_').replace(')', '')
        try:
            return ureg.Quantity(float(match_numerical.group(1)), unit)
        except pint.UndefinedUnitError:
            print(f'Unit not recognized: {match_numerical.group(2)}, parameter not converted: {value_str}')
            return value_str

    elif match_numerical:
        # No unit detected, value is just number
        if '.' not in value_str:
            # No decimal, convert to integer
            return int(value_str)
        else:
            # Decimal present, convert to float
            return float(value_str)

    # Check if value is a list of numbers with possible unit
    match_multiple_numerical = RE_MULTIPLE_NUMERICAL.match(value_str)
    if match_multiple_numerical:
        # List of values separated by space, possibly with a unit
        values = match_multiple_numerical.group(1).split()
        values = [float(x) for x in values]

        if match_multiple_numerical.group(2):
            unit = match_multiple_numerical.group(2)
            unit = unit.replace('~m', 'µm') if unit else None
            values = Quantity(values, unit)

        return values

    # Try if value is a date
    try:
        value = datetime.strptime(value_str, '%I:%M:%S %p %a %b %d %Y')
        return value
    except ValueError:
        pass

    # No other matches, strip " and return value
    return value_str.strip('"')
