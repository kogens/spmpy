import re
from dataclasses import dataclass
from os import PathLike
from pathlib import Path


# Regex for CIAO parameters (lines starting with \@ )
CIAO_REGEX = re.compile(
    r'^\\?@(?:(?P<group>\d?):)?(?P<param>.*): (?P<type>\w)\s?(?:\[(?P<softscale>.*)\])?\s?(?:\((?P<hardscale>.*)\))?\s?(?P<hardval>.*)$')


class SPMFile:
    """ Representation of an entire SPM file with images and metadata """

    def __init__(self, path: str | PathLike):
        self.path = Path(path)
        self.metadata = None
        self.images = None

        self.load_spm()

    def load_spm(self):
        """ Load an SPM file and extract images and metadata """
        with open(self.path, 'rb') as f:
            file_bytes = f.read()

        metadata_lines = extract_metadata_lines(file_bytes)
        interpret_metadata(metadata_lines)


class CIAOImage:
    """ A CIAO image with metadata"""
    ...


@dataclass
class CIAOParameter:
    r""" CIAO parameters are lines in the SPM file starting with \@ and have several "sub" parameters """
    parameter: str
    ptype: str
    value: float | str

    group: int = None
    unit: str = ''
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
                self.sscale = match.group('softscale')
                self.hscale = float(match.group('hardscale').split()[0]) if match.group('hardscale') else None
                self.value = float(match.group('hardval').split()[0]) if match.group('hardval') else None
                self.unit = match.group('hardval').split()[1] if len(match.group('hardval').split()) > 1 else None
            elif self.ptype == 'S':
                # "Select" parameter
                self.internal_designation = match.group('softscale').strip('"')
                self.external_designation = match.group('hardval').strip('"')
                self.value = match.group('hardval').split()[0].strip('"') if match.group('hardval') else None
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

    # Wa
    for line in metadata_lines:
        if line.startswith('*'):
            # Lines starting with * indicate a new section
            current_section = line.strip('*')

            if current_section == 'Ciao image list':
                # "Ciao image list" appears multiple times so we give them a number
                current_section = f'Ciao image list {n_image}'
                n_image += 1

            # Initialize an empty dict for each section
            metadata[current_section] = {}

        elif line.startswith('@'):
            # Line is CIAO parameter, interpret and add to current section
            parameter = CIAOParameter(line)
            print(parameter)

        else:
            # Line is regular parameter, add to metadata of current seciton
            key, value = line.split(':', 1)
            metadata[current_section][key] = value.strip()

    return None


if __name__ == '__main__':
    # Load data as raw bytes and lines
    datapath = Path.home() / 'Data' / 'afm_testfile.spm'
    spm_data = SPMFile(datapath)
    print(spm_data.metadata)

    # Plot images in SPM file
    # fig, ax = plt.subplots(ncols=len(spm_data.images))
    # for j, image in enumerate(spm_data.images.values()):
    #     ax[j].imshow(image)
    #
    # plt.show()
