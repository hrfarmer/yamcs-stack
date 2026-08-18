# PROVES runtime bundle

Generate this directory from the firmware checkout:

```sh
cd ~/code/spacelab/proves-core-reference
make yamcs-export
```

The resulting, gitignored bundle contains:

- `fprime-dictionary.json`: the F Prime topology dictionary for the flashed build.
- `auth-key.hex`: the matching 16-byte HMAC key, encoded as 32 hexadecimal characters.

Never mix files from different firmware builds and never commit the key.

