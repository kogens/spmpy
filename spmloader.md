# Load SPM files from Bruker AFM Microscopes
## Setup
Requires Python >= 3.9 with `numpy` and [`pint`](https://pint.readthedocs.io/) for units, 
optionally `matplotlib` for plotting examples below
```
pip install numpy pint matplotlib
```

Place the `spmloader.py` file with your script and import the `SPMFile` class.

## Loading SPM files
The SPM file can be loaded by passing a file path directly to `SPMFile`:

```python
from spmloader import SPMFile

# Pass a path to SPMFile to load the data
spm_data = SPMFile('afm_testfile.spm')
print(spm_data)
```

This should give something like::
```
SPM file: "afm_testfile.spm", 2023-05-24 10:27:35. Images: ['ZSensor', 'AmplitudeError', 'Phase']`
```

All the metadata is accessible as `spm_data.metadata` where it is organized in the sections starting with "*" in the 
SPM file. You can also access any parameter directly by treating `spm_data` as a dict, e.g. 
```python
scan_size = spm_data['Scan Size']
z_sensor_scaling = spm_data['Sens. ZsensSens']
```

Each parameter in the metadata is automatically interpreted into relevant datatypes (numbers, strings etc). 
Any parameter with units is represented as a `Quantity` from the [Pint](https://pint.readthedocs.io/) library.



### Images in the SPM file
An SPM file usually has more than one image and can be accessed with `spm_data.images`:
```
{'AmplitudeError': AFM image "AmplitudeError" [mV·nm/V], (128, 128) px = (4500.0, 4500.0) nm,
 'Phase': AFM image "Phase" [degree], (128, 128) px = (4500.0, 4500.0) nm,
 'ZSensor': AFM image "ZSensor" [nm], (128, 128) px = (4500.0, 4500.0) nm}
```

These images have already been converted to physical units and  can be plotted directly:
```python
import matplotlib.pyplot as plt

height_im = spm_data.images['ZSensor']
plt.imshow(height_im)
plt.show()
```

This will show the image with pixels on x and y-axis. For `imshow()` you can set an `extent` to show the physical units.
Either calculate the extent with `image.px_size_x` and `image.px_size_y` or use the built in `image.extent` value.
The coordinates are also available as `image.x`, `image.y` or meshgrids in `image.meshgrid`.
```python
# Plot the height image with units
im = plt.imshow(height_im, extent=height_im.extent)
plt.title(height_im.title)
plt.xlabel(height_im.x.units)
plt.ylabel(height_im.y.units)
cbar = plt.colorbar(im)
cbar.set_label(f'{height_im["Image Data"]} [{height_im.units}]')
plt.show()
```

The underlying data is stored as Pint `Quantity` objects which handles the units and support most of the same operations
as a numpy `ndarray`, such as addition, multiplication etc. with other images although this will currently strip the 
metadata
```python
min_value, max_value = height_im.min(), height_im.max()

# Set zero point at the lowest value
height_im_zeroed = height_im - min_value

# Normalize image to have values from 0 to 1
height_im_normalized = (height_im-min_value)/(max_value-min_value)
```

For some operations you need the pure Numpy `ndarray`, e.g. for many [scikit-image](https://scikit-image.org/) functions, and 
this can be accessed using the `.magnitude` or `.m` attribute:
```python
# Get underlying numpy ndarray
raw_numpy_array = height_im.m
print(type(raw_numpy_array))
```


All images found in the SPM file, such as phase and amplitude error images
can be plotted like so
```python
# Plot images in SPM file
fig, ax = plt.subplots(ncols=len(spm_data.images))
for i, image in enumerate(spm_data.images.values()):
    im = ax[i].imshow(image, extent=image.extent)
    ax[i].set_title(image.title)
    ax[i].set_xlabel(image.x.units)
    ax[i].set_ylabel(image.y.units)

plt.tight_layout()
plt.show()
```




---
# Excerpts from the Nanoscope User Guide
A copy of the Nanoscope 8.10 User Guide can be found at [NanoQAM](http://nanoqam.ca/wiki/lib/exe/fetch.php?media=nanoscope_software_8.10_user_guide-d_004-1025-000_.pdf).
Specifically appendix A.3 and A.7 are of interest


## General Format for CIAO Parameter Objects
In the file header some parameters start with `\@` instead of simply `\\`. This is an indication to the software
that the data that follows is intended for a CIAO parameter object. After the `@`, you might see a number
followed by a colon before the label. This number is what we call a “group number” and can generally be
ignored.

Further, after the label and its colon, you will see a single definition character of `V`, `C`, or `S`.

- `V` means Value – a parameter that contains a double and a unit of measure, and some scaling
definitions.
- `C` means Scale – a parameter that is simply a scaled version of another.
- `S` means Select – a parameter that describes some selection that has been made.


### Value parameters
The Value (identified by the letter “V”) parameters have the following format:

`[soft-scale] (hard-scale) hard-value`

##### Example: Value parameter format
```
Group    Parameter type
  |          | 	
\@1:Z limit: V [Sens. Zsens] (0.006714 V/LSB) 440.0 V
    ^^^^^^^    ^^^^^^^^^^^^^  ^^^^^^^^^^^^^^  ^^^^^^^
   Parameter   Soft scale      Hard scale    Hard value
```

#### LSB
Since the NanoScope is a digital device, all data is numeric. We call this number in its rawest form a LSB
(i.e., scaling values on ADCs and DACs as Volts per Least-Significant-Bit). The LSB is the digital
representation of volts or frequency and is a 16 bit integer.

#### Hard value
The hard value is the analog representation of a measurement. It is simply the value read on the parameter
panel when you set the Units: to Volts. The hard-value is the value you would read with a voltmeter inside of
the NanoScope electronics or inside the head. This value is always in volts with the exception of the Drive
Frequency (which is in Hertz) and some STM parameters (which are in Amps).
A value parameter might be missing a soft-scale or a hard-scale, but must always have a hard-value.

#### Hard Scale
The hard scale is the conversion factor we use to convert LSBs into hard values. We use the prefix “hard-” in
hard-scale and hard-value because these numbers are typically defined by the hardware itself and are not
changeable by the user.

#### Soft Value
A soft-value is what the user sees on the screen when the Units: are set to Metric.

#### Soft Scale
The soft-scale is what we use to convert a hard-value into a soft-value. Soft-scales are user defined, or are
calibration numbers that the user divines. Soft-scales in the parameters are typically not written out —
rather, another tag appears between the brackets, like [Sens. Zsens]. In that case, you look elsewhere in the
parameter list for tag and use that parameter's hard-value for the soft-scale.

**Note:** The name of a soft scale can change from one microscope, controller or software version to the
next. A common problem occurs when users create programs that look for the soft scale
directly instead of parsing the value parameter to find the name of the soft scale that must be
used.


### Scale Parameters
The Scale parameters (identified by the letter “C”) have the following format:

```[soft-scale] hard-value```

#### Example: Scale parameter format
```
      Parameter type     Hard value
             |             vvvvv
\@Z magnify: C [2:Z scale] 0.599
  ^^^^^^^^^     ^^^^^^^^^  
  Parameter     Soft scale 
```

- The hard-value is almost always a scalar value.
- The soft-scale always points to another parameter – this parameter is the target of the scaling
action.
- Most often used for the Z magnify parm to allow user to change scaling of Z scale in Off-
line without actually affecting the real data in the file.


### Select Parameters
The Select parameters (identified by the letter “S”) have the following format:
```
[Internal-designation for selection] “external-designation for selection"
```

#### Example: Select parameter format
```
Group number   Parameter type
  |             |     External designation
  |             |           vvvvvv
\@2:Image Data: S [Height] "Height"
    ^^^^^^^^^^     ^^^^^^
    Parameter   Internal-designation
```