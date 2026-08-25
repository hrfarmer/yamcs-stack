# PROVES ground-station runtime bundle

Copy the matching export from firmware (or the server fixtures for local tests):

```sh
cd ~/code/spacelab/proves-core-reference
make yamcs-export
# then copy into client/inputs/proves/
```

Required files:

- `fprime-dictionary.json`
- `auth-key.hex` (HMAC key used when wrapping telecommands for the radio)

Never commit the key.
