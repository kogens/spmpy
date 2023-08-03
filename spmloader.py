import re
import struct
from dataclasses import dataclass
from datetime import datetime
from os import PathLike
from pathlib import Path

import numpy as np
import pint
from matplotlib import pyplot as plt

# Regex for CIAO parameters (lines starting with \@ )
CIAO_REGEX = re.compile(
    r'^\\?@(?:(?P<group>\d?):)?(?P<param>.*): (?P<type>\w)\s?(?:\[(?P<softscale>.*)\])?\s?(?:\((?P<hardscale>.*)\))?\s?(?P<hardval>.*)$')


class SPMFile:
    """ Representation of an entire SPM file with images and metadata """

    def __init__(self, path: str | PathLike):
        self.path = Path(path)
        self.metadata, self.images = self.load_spm()
        self.date = datetime.strptime(self.metadata['File list']['Date'], '%I:%M:%S %p %a %b %d %Y')

    def __str__(self):
        titles = [x.title for x in self.images]
        return f'SPM file: "{self.path.name}", {self.date}. Images: {titles}'

    def load_spm(self):
        """ Load an SPM file and extract images and metadata """
        with open(self.path, 'rb') as f:
            file_bytes = f.read()

        metadata_lines = extract_metadata_lines(file_bytes)
        metadata = interpret_metadata(metadata_lines)
        images = extract_ciao_images(metadata, file_bytes)

        return metadata, images


class CIAOImage:
    """ A CIAO image with metadata"""

    def __init__(self, image_metadata: dict, full_metadata: dict, file_bytes: bytes):
        self.metadata = image_metadata

        # Data offset and length refer to the bytes of the original file including metadata
        data_start = int(image_metadata['Data offset'])
        data_length = int(image_metadata['Data length'])

        # Calculate the number of pixels in order to decode the bytestring
        n_rows, n_cols = int(image_metadata['Number of lines']), int(image_metadata['Samps/line'])
        n_pixels = n_cols * n_rows

        # Note: The byte lengths don't seem to follow the bytes/pixel defined in the metadata.
        # bytes_per_pixel = int(image_section['Bytes/pixel'])
        # n_pixels2 = data_length // bytes_per_pixel

        # Extract image data from the raw bytestring of the full file
        bytedata = file_bytes[data_start: data_start + data_length]

        # Decode the byte values as signed 32-bit integers (despite documentation saying 16-bit signed int)
        # https://docs.python.org/3/library/struct.html#format-characters
        pixel_values = struct.unpack(f'{n_pixels}i', bytedata)

        # Reorder image into a numpy array. Note that i, j might have to be switched, not sure how to test that
        self.image = np.array(pixel_values).reshape(n_rows, n_cols)
        self.image_physical, self.pixel_size_x, self.pixel_size_y = self.get_physical_units(full_metadata)
        self.title = self.metadata['2:Image Data'].internal_designation

    def get_physical_units(self, full_metadata: dict):
        z_scale = self.metadata['2:Z scale']
        hard_value = z_scale.value
        soft_scale_key = z_scale.sscale
        soft_scale_value = full_metadata['Ciao scan list'][soft_scale_key].value

        # Note: Documentation says divide by 2^16, but 2^32 gives proper results...?
        corrected_hard_scale = hard_value / 2 ** 32

        corrected_image = self.image * corrected_hard_scale * soft_scale_value

        # Calculate pixel sizes in physical units
        # NOTE: This assumes "Scan Size" always represents the longest dimension of the image.
        n_rows, n_cols = self.image.shape
        aspect_ratio = (max(self.image.shape) / n_rows, max(self.image.shape) / n_cols)
        scansize = full_metadata['Ciao scan list']['Scan Size']

        pixel_size_rows = scansize / (n_rows - 1) / aspect_ratio[0]
        pixel_size_cols = scansize / (n_cols - 1) / aspect_ratio[1]

        return corrected_image, pixel_size_cols, pixel_size_rows


@dataclass
class CIAOParameter:
    r""" CIAO parameters are lines in the SPM file starting with \@ and have several "sub" parameters """
    parameter: str
    ptype: str
    value: float | str

    group: int = None
    hscale: float = None
    sscale: str = None
    internal_designation: str = None
    external_designation: str = None

    def __init__(self, ciao_string: str):
        match = CIAO_REGEX.match(ciao_string)
        if match:
            self.group = int(match.group('group')) if match.group('group') else None
            self.parameter = match.group('param')
            self.ptype = match.group('type')

            if self.ptype in ['V', 'C']:
                # "Value" or "Scale" parameter
                self.sscale = parse_parameter(match.group('softscale'))
                self.hscale = parse_parameter(match.group('hardscale')) if match.group('hardscale') else None
                self.value = parse_parameter(match.group('hardval')) if match.group('hardval') else None
            elif self.ptype == 'S':
                # "Select" parameter
                self.internal_designation = parse_parameter(match.group('softscale'))
                self.external_designation = parse_parameter(match.group('hardval'))
                self.value = parse_parameter(match.group('hardval')) if match.group('hardval') else None
        else:
            raise ValueError(f'Not a recognized CIAO parameter object: {ciao_string}')


def extract_metadata_lines(spm_bytestring: bytes) -> list[str]:
    """ Extract the metadata section between "*File list" and "*File list end" and decode and cleanup the lines """
    # Extract lines as list of bytestrings
    file_lines = spm_bytestring.splitlines()

    start_index = 0
    end_index = 0
    for i, line in enumerate(file_lines):
        if line.strip() == b'\\*File list':
            start_index = i
        elif line.strip() == b'\\*File list end':
            end_index = i
            break

    # Extract the identified lines between start and end. Decode strings and strip unwanted characters.
    metadata_lines = [x.decode('latin-1').lstrip('\\').strip() for x in file_lines[start_index:end_index]]

    return metadata_lines


def interpret_metadata(metadata_lines: list[str]):
    """ Walk through all lines in metadata and interpret sections beginning with * """
    metadata = {}
    current_section = None
    n_image = 0

    # Walk through each line of metadata and extract sections and parameters
    for line in metadata_lines:
        if line.startswith('*'):
            # Lines starting with * indicate a new section
            current_section = line.strip('*')

            # "Ciao image list" appears multiple times, so we give them a number
            if current_section == 'Ciao image list':
                current_section = f'Ciao image list {n_image}'
                n_image += 1

            # Initialize an empty dict to contain metadata for each section
            metadata[current_section] = {}

        elif line.startswith('@'):
            # Line is CIAO parameter, interpret and add to current section
            ciaoparam = CIAOParameter(line)

            # Note: The "parameter" used as key is not always unique and can appear multiple times with different
            # group number. Usually not an issue for CIAO images, however.
            key = ciaoparam.parameter if not ciaoparam.group else f'{ciaoparam.group}:{ciaoparam.parameter}'
            metadata[current_section][key] = ciaoparam
        else:
            # Line is regular parameter, add to metadata of current section
            key, value = line.split(':', 1)
            metadata[current_section][key] = parse_parameter(value)

    return metadata


def extract_ciao_images(metadata: dict, file_bytes: bytes):
    """ Data for CIAO images are found using the metadata from the Ciao image sections in the metadata """
    images = []
    image_sections = {k: v for k, v in metadata.items() if k.startswith('Ciao image')}
    for i, image_section in enumerate(image_sections.values()):
        images.append(CIAOImage(image_section, metadata, file_bytes))

    return images


# Define regex to identify numerical values and UnitRegistry for handling units.
#
NUMERICAL_REGEX = re.compile(r'([+-]?\d+\.?\d*)( [\wº~/]+)?$')
ureg = pint.UnitRegistry()
ureg.define('LSB = Least Significant Bit = 1')
ureg.define('Arb = 1')
ureg.define('º = deg = degree')


def parse_parameter(parameter_string):
    """ Parse parameters into either int, float, string or physical quantity """
    if not parameter_string:
        return parameter_string

    value_str = parameter_string.strip(' "')
    match_numerical = NUMERICAL_REGEX.match(value_str)

    if not match_numerical:
        # Parameter is not numerical, return string
        return value_str
    elif match_numerical.group(2) is None:
        # No unit detected, value is just number
        if '.' not in value_str:
            # No decimal, convert to integer
            return int(value_str)
        else:
            # Decimal present, convert to float
            return float(value_str)
    else:
        # Value with unit
        return ureg.Quantity(value_str)


if __name__ == '__main__':
    # Load data as raw bytes and lines
    datapath1 = Path.home() / 'Data' / 'afm_testfile1.spm'
    datapath2 = Path.home() / 'Data' / 'afm_testfile2.spm'
    # spm_data1 = SPMFile(datapath1)
    spm_data2 = SPMFile(datapath2)

    print(spm_data2)

    # Plot images in SPM file
    fig, ax = plt.subplots(ncols=len(spm_data2.images))
    for j, image in enumerate(spm_data2.images):
        ax[j].imshow(image.image_physical)
        ax[j].set_title(image.title)

    plt.tight_layout()
    plt.show()
