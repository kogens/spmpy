from __future__ import annotations

import struct
import warnings
from os import PathLike
from pathlib import Path

import numpy as np
from pint import Quantity

from .ciaoparams import CIAOParameter
from .utils import parse_parameter_value

SUPPORTED_VERSIONS = ['0x09200201', '0x09400202', '0x09400103']


class SPMFile:
    """ Representation of an entire SPM file with images and metadata """

    def __init__(self, spmfile: str | PathLike | bytes, encoding='latin-1'):
        if isinstance(spmfile, (str, Path)):
            self.path = Path(spmfile)
            bytestring = self.load_from_file(spmfile)
        elif isinstance(spmfile, bytes):
            bytestring = spmfile
        else:
            raise ValueError('SPM file must be path to spm file or raw bytestring')

        self.header: dict = self.parse_header(bytestring, encoding=encoding)
        file_version = self.header['File list']['Version']
        if file_version not in SUPPORTED_VERSIONS:
            warnings.warn(f'Untested SPM file verison, calculations may be inaccurate: {file_version}. '
                          f'Supported versions; {SUPPORTED_VERSIONS}')
        self.images: dict = self.extract_ciao_images(self.header, bytestring)

    def __repr__(self) -> str:
        titles = [x for x in self.images.keys()]
        path = f'{self.path.name}, ' if hasattr(self, 'path') else ''
        return f'SPM file: {path}{self["Date"]}. Images: {titles}'

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
    def parse_header(bytestring, encoding) -> dict:
        """ Extract data in header from bytes """
        return parse_header(bytestring, encoding=encoding)

    @staticmethod
    def extract_ciao_images(header: dict, bytestring: bytes) -> dict[str, CIAOImage]:
        """ Data for CIAO images are found using each image header from the Ciao image section in the file header """
        images = {}
        image_sections = header['Ciao image list']
        for i, image_header in enumerate(image_sections):
            image = CIAOImage(bytestring, header, image_number=i)
            key = image_header['2:Image Data'].external_designation
            images[key] = image

        return images


class CIAOImage:
    """ A CIAO image with header """

    def __init__(self, file_bytes: bytes, file_header: dict, image_number: int):
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
        self.scan_size = self.file_header['Ciao scan list']['Scan Size']

        # Calculate relevant physical quantities for dimension
        n_rows, n_cols = self._raw_image.shape
        aspect_ratio = [float(x) for x in self['Aspect Ratio'].strip().split(':')]

        # Height and width
        self.height = self.scan_size / aspect_ratio[0]
        self.width = self.scan_size / aspect_ratio[1]

        # Size of pixels in x and y
        self.px_size_y = self.height / n_rows
        self.px_size_x = self.width / n_cols

        self.y = np.linspace(0, self.height, n_rows)
        self.x = np.linspace(0, self.width, n_cols)

    @property
    def _bytes_per_pixel(self):
        """
        Calculate bytes/pixel based on data length and number of rows and columns.
        Note: This is often different from the "Bytes/pixel" parameter in the header, but this seems to be the
        correct way to identify it.
        See also discussion from pySPM
        https://github.com/scholi/pySPM/issues/1
        """
        data_length = self.image_header['Data length']
        n_rows, n_cols = self.image_header['Number of lines'], self.image_header['Samps/line']

        return data_length // (n_rows * n_cols)

    def raw_image_from_bytes(self, file_bytes):
        """
        Decode image bytes into raw pixel values (unscaled, no units).

        See discussion from Gwyddion mailing list
        https://sourceforge.net/p/gwyddion/mailman/gwyddion-users/thread/YyCVZDIMBXv7CgC5%40physics.muni.cz/#msg37706696
        """
        # Data offset and length refer to the bytes of the original file including header
        data_start = self.image_header['Data offset']
        data_length = self.image_header['Data length']

        # Calculate the number of pixels in order to decode the bytestring.
        n_rows, n_cols = self.image_header['Number of lines'], self.image_header['Samps/line']
        n_pixels = n_rows * n_cols

        # Construct a dict for translating bytes/pixel into the corresponding letter for struct
        # https://docs.python.org/3/library/struct.html#format-characters
        bpp = {2: 'h', 4: 'i', 8: 'q'}[self._bytes_per_pixel]
        bytestring = file_bytes[data_start: data_start + data_length]

        # Decode raw pixel values from bytes
        pixel_values = struct.unpack(f'<{n_pixels}{bpp}', bytestring)

        # Reorder image into a numpy array and calculate the physical value of each pixel
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
        if z_scale.soft_scale:
            sens_z_scan = self[z_scale.soft_scale]
            z_height = self._raw_image * z_scale.hard_value * sens_z_scan.hard_value / 2 ** (8 * self._bytes_per_pixel)
        else:
            z_height = self._raw_image * z_scale.hard_value / 2 ** (8 * self._bytes_per_pixel)

        return z_height

    @property
    def _flat_header(self):
        """ Construct a "flat header" for accessing with __getitem__. """
        flat_header = {k: v for key in self.file_header.keys() for k, v in self.file_header[key].items()}
        flat_header.update(self.image_header)

        return flat_header

    def __array__(self) -> np.ndarray:
        """ Return image as numpy array """
        # warnings.filterwarnings("ignore", category=UnitStrippedWarning)
        return self.image.__array__()

    def __array_ufunc__(self, ufunc, method, *inputs, **kwargs):
        """ Allow numpy ufuncs to be applied to image """
        return getattr(self.image, '__array_ufunc__')(ufunc, method, *inputs, **kwargs)

    def __getitem__(self, key) -> str | int | float | Quantity:
        """ Fetches values from the header when class is called like a dict """
        return self._flat_header[key]

    def __repr__(self) -> str:
        """ Representation of CIAO image """
        reprstr = (f'{self.image_header["Data type"]} image "{self.title}" [{self.image.units}], '
                   f'{self.image.shape} px = ({self.height.m:.1f}, {self.width.m:.1f}) {self.px_size_x.u}')
        return reprstr

    def __str__(self):
        return self.__repr__()

    def __getattr__(self, name):
        """ Get attributes from the image, i.e. directly from the Quantity or numpy array containing the image data"""
        return getattr(self.image, name)

    def __add__(self, other):
        if isinstance(other, CIAOImage):
            return self.image + other.image
        else:
            return self.image + other

    def __radd__(self, other):
        return self.__add__(other)

    def __sub__(self, other):
        if isinstance(other, CIAOImage):
            return self.image - other.image
        else:
            return self.image - other

    def __rsub__(self, other):
        return self.__sub__(other)

    def __mul__(self, other):
        if isinstance(other, CIAOImage):
            return self.image * other.image
        else:
            return self.image * other

    def __rmul__(self, other):
        return self.__mul__(other)

    def __truediv__(self, other):
        if isinstance(other, CIAOImage):
            return self.image / other.image
        else:
            return self.image / other

    def __rtruediv__(self, other):
        return self.__truediv__(other)

    @property
    def extent(self) -> list[float]:
        """ Extent of image in physical units """
        ext = [0, self.width.magnitude, 0, self.height.magnitude]
        return ext

    @property
    def meshgrid(self) -> np.meshgrid:
        """ Meshgrid of x and y coordinates """
        return np.meshgrid(self.x, self.y)

    def plot(self, ax=None, add_cbar=True, **kwargs):
        """ Plot image with matplotlib """
        if ax is None:
            # Get current axis if none is specified
            # If matplotlib.pyplot is not imported, import it and create a new axis
            import matplotlib.pyplot as plt
            ax = plt.gca()

        # Plot image and set axis labels
        im = ax.imshow(self.image.m, extent=self.extent, **kwargs)
        ax.set_xlabel(self.width.units)
        ax.set_ylabel(self.height.units)
        ax.set_title(self.title)

        if add_cbar:
            # Add colorbar to axis with units
            cbar = ax.figure.colorbar(im, ax=ax, fraction=0.05, pad=0.1)
            cbar.ax.set_ylabel(self.image.units)

        return ax


def parse_header(header_bytestring: bytes, encoding: str) \
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
