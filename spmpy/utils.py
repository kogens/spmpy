from __future__ import annotations

import re
from datetime import datetime

from pint import UnitRegistry, Quantity, UndefinedUnitError

# Define regex to identify numerical values
RE_NUMERICAL = re.compile(r'^([+-]?\d+\.?\d*(?:[eE][+-]\d+)?)( 1?[^\d:]+)?$')
RE_MULTIPLE_NUMERICAL = re.compile(r'^((?:(?<!\d)[+-]?\d+\.?\d*(?:[eE][+-]\d+)? )+)([^\d:]+)?$')
RE_DATE = re.compile(r'^\d{2}:\d{2}:\d{2} (AM|PM) \D{3} \D{3} \d{1,2} \d{4}$')

# Pint UnitRegistry handles physical quantities
ureg = UnitRegistry()
ureg.define(f'least_significant_bit  = 1 = LSB')
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

LSB is the "voltage resolution" of the ADC/DAC and represents the smallest change in voltage that can be measured.

"""


def parse_parameter_value(value_str: str) -> str | int | float | Quantity | datetime | None:
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
        except UndefinedUnitError:
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

    match_date = RE_DATE.match(value_str)
    if match_date:
        return datetime.strptime(value_str, '%I:%M:%S %p %a %b %d %Y')

    # No other matches, strip " and return value
    return value_str.strip('"')
