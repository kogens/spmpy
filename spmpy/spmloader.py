from __future__ import annotations

import struct
from os import PathLike
from pathlib import Path

import numpy as np
from pint import Quantity

from .ciaoparams import CIAOParameter
from .utils import parse_parameter_value

# Integer size used when decoding data from raw bytestrings
INTEGER_SIZE = 2 ** 32


class SPMFile:
    """ Representation of an entire SPM file with images and metadata """

    def __init__(self, spmfile: str | PathLike | bytes):
        if isinstance(spmfile, (str, Path)):
            self.path = Path(spmfile)
            bytestring = self.load_from_file(spmfile)
        elif isinstance(spmfile, bytes):
            bytestring = spmfile
        else:
            raise ValueError('SPM file must be path to spm file or raw bytestring')

        self.header: dict = self.parse_header(bytestring)
        self.images: dict = self.extract_ciao_images(self.header, bytestring)

    def __repr__(self) -> str:
        titles = [x for x in self.images.keys()]
        return f'SPM file: {self["Date"]}. Images: {titles}'

    def __getitem__(self, item) -> tuple[int, float, str, Quantity]:
        """ Fetches values from the header when class is called like a dict """
        return self._flat_header[item]

    @property
    def _flat_header(self):
        """ Construct a "flat header" for accessing with __getitem__.
        Avoid "Ciao image list" as it appears multiple times with non-unique keys """
        non_repeating_keys = [key for key in self.header.keys() if 'Ciao image list' not in key]
        return {k: v for key in non_repeating_keys for k, v in self.header[key].items()}

    @property
    def groups(self) -> dict[int | None, dict[str]]:
        """ CIAO parameters ordered by group number """
        groups = {}
        for key, value in sorted(self._flat_header.items()):
            if isinstance(value, CIAOParameter):
                if value.group in groups.keys():
                    groups[value.group].update({key.split(':', 1)[-1]: value})
                else:
                    groups[value.group] = {}
                    groups[value.group].update({key.split(':', 1)[-1]: value})

        return groups

    @staticmethod
    def load_from_file(path):
        """ Load SPM data from a file on disk """
        with open(path, 'rb') as f:
            bytestring = f.read()

        return bytestring

    @staticmethod
    def parse_header(bytestring) -> dict:
        """ Extract data in header from bytes """
        return interpret_file_header(bytestring)

    @staticmethod
    def extract_ciao_images(header: dict, bytestring: bytes) -> dict[str, CIAOImage]:
        """ Data for CIAO images are found using each image header from the Ciao image section in the file header """
        images = {}
        image_sections = header['Ciao image list']
        for i, image_header in enumerate(image_sections):
            image = CIAOImage(bytestring, header, image_number=i)
            key = image_header['2:Image Data'].internal_designation
            images[key] = image

        return images


class CIAOImage:
    """ A CIAO image with header """

    # TODO: Validate conversion from bytes to physical units
    # TODO: Revisit how the "corrected zscale" is fetched from the file header
    # TODO: Revisit how aspect ratio is used so we don't elongate images

    def __init__(self, file_bytes: bytes, file_header: dict, image_number: int, int_size: int = INTEGER_SIZE):
        self._integer_size = int_size
        try:
            # Get appropriate image header from the list of images based on image_number
            self.image_header = file_header['Ciao image list'][image_number]
        except IndexError as e:
            raise IndexError('CIAO image not in header, specify image number '
                             f'between 0 and {len(file_header["Ciao image list"]) - 1}') from e

        # Drop all Ciao image headers from the file header
        self.file_header = {key: value for key, value in file_header.items() if key not in 'Ciao image list'}

        # Convert bytes into raw pixel values (without physical units)
        self._raw_image = self.raw_image_from_bytes(file_bytes)

        # Some handy attributes are defined below for ease of use
        self.title = self.image_header['2:Image Data'].external_designation
        self.scansize = self._flat_header['Scan Size']

        self.width = None
        self.height = None
        # self.data = None

        self.px_size_x, self.px_size_y = None, None
        self.x, self.y = None, None

        self.calculate_physical_units()

    def raw_image_from_bytes(self, file_bytes):
        # Data offset and length refer to the bytes of the original file including header
        data_start = self.image_header['Data offset']
        data_length = self.image_header['Data length']

        # Calculate the number of pixels in order to decode the bytestring.
        # Note: "Bytes/pixel" is defined in the header but byte lengths don't seem to follow the bytes/pixel it.
        n_rows, n_cols = self.image_header['Number of lines'], self.image_header['Samps/line']
        n_pixels = n_cols * n_rows

        # Extract relevant image data from the raw bytestring of the full file and decode the byte values
        # as signed 32-bit integers in little-endian (same as "least significant bit").
        # Note that this is despite documentation saying 16-bit signed int.
        # https://docs.python.org/3/library/struct.html#format-characters
        bytestring = file_bytes[data_start: data_start + data_length]
        pixel_values = struct.unpack(f'<{n_pixels}i', bytestring)

        # Reorder image into a numpy array and calculate the physical value of each pixel.
        # Row order is reversed in stored data, so we flip up/down.
        raw_image = np.flipud(np.array(pixel_values).reshape(n_rows, n_cols))

        return raw_image

    @property
    def image(self):
        """
        Image data with physical units.

        From manual:
            To convert raw data into metric units, use the following relation:
                Z height = (data point value)(Z scale)(Sens. Zscan)/2^16

            Note: The Z scale value in a parameter list includes the value and the units (for example, \\Z scale:
            1.57541 µm). In this example, the units of measure are in microns (µm).
        """
        # z_scale_key = self['Z magnify'].soft_scale  # Possibly more general way to get "2:Z Scale" key
        z_scale = self['2:Z scale']
        sens_z_scan = self[z_scale.soft_scale]

        z_height = self._raw_image * z_scale.hard_value * sens_z_scan.hard_value / self._integer_size

        return z_height

    @property
    def _flat_header(self):
        """ Construct a "flat header" for accessing with __getitem__. """
        flat_header = {k: v for key in self.file_header.keys() for k, v in self.file_header[key].items()}
        flat_header.update(self.image_header)

        return flat_header

    def calculate_physical_units(self):
        """
        Calculate physical scale of image values.

        From manual:
            To obtain the X axis pixel width, use the following relation:
                X = Scan size / ((Samples/line) - 1)
                Y = Scan size / ((Number of Lines) - 1)

        """
        # Calculate pixel sizes in physical units
        # NOTE: This assumes "Scan Size" always represents the longest dimension of the image.
        n_rows, n_cols = self._raw_image.shape
        aspect_ratio = (max(self._raw_image.shape) / n_rows, max(self._raw_image.shape) / n_cols)

        # Calculate pixel sizes and
        self.px_size_y = self.scansize[0] / (n_rows - 1) / aspect_ratio[0]
        self.px_size_x = self.scansize[1] / (n_cols - 1) / aspect_ratio[1]

        self.height = (n_rows - 1) * self.px_size_y
        self.width = (n_cols - 1) * self.px_size_x

        self.y = np.linspace(0, self.height, n_rows)
        self.x = np.linspace(0, self.width, n_cols)

    def __array__(self) -> np.ndarray:
        # warnings.filterwarnings("ignore", category=UnitStrippedWarning)
        return self.data.__array__()

    def __array_ufunc__(self, ufunc, method, *inputs, **kwargs):
        return getattr(self.data, '__array_ufunc__')(ufunc, method, *inputs, **kwargs)

    def __getitem__(self, key) -> str | int | float | Quantity:
        """ Access parameters directly by key from flattened header """
        return self._flat_header[key]

    def __repr__(self) -> str:
        reprstr = (f'{self.image_header["Data type"]} image "{self.title}" [{self.data.units}], '
                   f'{self.data.shape} px = ({self.height.m:.1f}, {self.width.m:.1f}) {self.px_size_x.u}')
        return reprstr

    def __str__(self):
        return self.__repr__()

    def __getattr__(self, name):
        return getattr(self.data, name)

    def __add__(self, other):
        if isinstance(other, CIAOImage):
            return self.data + other.data
        else:
            return self.data + other

    def __radd__(self, other):
        return self.__add__(other)

    def __sub__(self, other):
        if isinstance(other, CIAOImage):
            return self.data - other.data
        else:
            return self.data - other

    def __rsub__(self, other):
        return self.__sub__(other)

    def __mul__(self, other):
        if isinstance(other, CIAOImage):
            return self.data * other.data
        else:
            return self.data * other

    def __rmul__(self, other):
        return self.__mul__(other)

    def __truediv__(self, other):
        if isinstance(other, CIAOImage):
            return self.data / other.data
        else:
            return self.data / other

    def __rtruediv__(self, other):
        return self.__truediv__(other)

    @property
    def extent(self) -> list[float]:
        ext = [0, self.width.magnitude, 0, self.height.magnitude]
        return ext

    @property
    def meshgrid(self) -> np.meshgrid:
        return np.meshgrid(self.x, self.y)


def interpret_file_header(header_bytestring: bytes, encoding: str = 'latin-1') \
        -> dict[str, dict[str, int, float, str, Quantity]]:
    """ Walk through all lines in header and interpret sections beginning with * """
    header_lines = header_bytestring.splitlines()

    header = {'Ciao image list': []}
    current_section = None
    current_header = {}
    n_image = 0

    # Walk through each line of header and extract sections and parameters
    for line_bytes in header_lines:
        line = line_bytes.decode(encoding).lstrip('\\')
        if line.startswith('*'):
            # Lines starting with * indicate a new section
            if current_section == 'Ciao image list':
                # If the section is an image list, append previous header section to relevant list
                header[current_section].append(current_header)
                n_image += 1
            elif current_section is not None:
                # If section is a "regular" section, add header entry with section as key
                header[current_section] = current_header

            if line_bytes == b'\\*File list end':
                # End of header, break out of loop
                break

            # Get current secition and initialize an empty dict to contain header for each section
            current_section = line.strip('*')
            current_header = {}

        elif line.startswith('@'):
            # Line is CIAO parameter, interpret and add to current section
            ciaoparam = CIAOParameter.from_string(line)

            # The key must include the group number as the same name can appear multiple times
            key = ciaoparam.name if not ciaoparam.group else f'{ciaoparam.group}:{ciaoparam.name}'
            current_header[key] = ciaoparam
        else:
            # Line is regular parameter, add to header of current section
            key, value = line.split(':', 1)
            current_header[key] = parse_parameter_value(value)

    return header
