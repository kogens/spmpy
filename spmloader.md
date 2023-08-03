# Load SPM files from Bruker AFM Microscopes
Requires `numpy` and `pint` (for units), optionally `matplotlib` for plotting examples below
```
pip install numpy pint matplotlib
```

```python
from pathlib import Path
import matplotlib.pyplot as plt

from spmloader import SPMFile

# Load spm file. The path can also just be a string.
datapath = Path.home() / 'Data' / 'afm_testfile.spm'
spm_data = SPMFile(datapath)

print(spm_data)
```

An SPM file usually has more than one image, and can be accesed as an attribute with `spm_data.images`:
print(spm_data.images)
```python
{'AmplitudeError': CIAO AFM image "Amplitude Error", shape: (128, 128), unit: millivolt * nanometer / volt,
 'Phase': CIAO AFM image "Phase", shape: (128, 128), unit: º,
 'ZSensor': CIAO AFM image "Height Sensor", shape: (128, 128), unit: nanometer}
 ```

The image data can be plottes like so
```python
# Plot images in SPM file
fig, ax = plt.subplots(ncols=len(spm_data.images))
for j, image in enumerate(spm_data.images.values()):
    ax[j].imshow(image.image, extent=image.extent.magnitude)
    ax[j].set_title(image.title)

plt.tight_layout()
plt.show()
```




---
# Excerpt from the official Nanoscope 8.10 User Guide
Copy found at [NanoQAM](http://nanoqam.ca/wiki/lib/exe/fetch.php?media=nanoscope_software_8.10_user_guide-d_004-1025-000_.pdf)


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

## Example lines
```
\@Sens. Zsens: V 33.97668 nm/V
\@Sens. CurrentSens: V 10.00000 nA/V
\@Sens. SECPMSens: V 100.0000 mV/V
\@Sens. Xsensor: V 6551.668 nm/V
\@Sens. Ysensor: V 6515.867 nm/V

\@TR Mode: S [] "Disabled"
\@VerticalGainControlList: S [] "Disabled"
\@SCM Feedback: S [] ""
\@2:HsdcChanCDataType: S [HsdcDataTypeOff] "Off"
\@3:HsdcChanCDataType: S [HsdcDataTypeOff] "Off"
\@2:HsdcChanDDataType: S [HsdcDataTypeOff] "Off"
\@3:HsdcChanDDataType: S [HsdcDataTypeOff] "Off"
\@2:HsdcTriggerChan: S [HsdcDataTypeHeight] "Height"
\@3:HsdcTriggerChan: S [HsdcDataTypeHeight] "Height"
\@2:SPMFeedbackList: S [SPMFb] "Amplitude"
\@3:SPMFeedbackList: S [SPMFb] "Amplitude"
```