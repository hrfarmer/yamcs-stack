# Yamcs configuration

Files under `etc/` are templates for the single `fprime-project` instance.
`make yamcs-dict` reads the exported F Prime dictionary, substitutes the CCSDS
spacecraft ID and fixed frame length, generates the XTCE MDB, and writes the
complete runtime tree to `runtime/config`.

Do not put secrets or generated mission databases in this directory.

