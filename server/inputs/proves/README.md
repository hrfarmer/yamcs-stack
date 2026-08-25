# PROVES server input bundle

Provide at least:

- `fprime-dictionary.json` — F Prime topology dictionary for XTCE generation

`auth-key.hex` is optional here (telecommand authentication happens on the
ground-station client). Generate the export from firmware:

```sh
cd ~/code/spacelab/proves-core-reference
make yamcs-export
```
