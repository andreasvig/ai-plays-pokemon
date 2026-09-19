# Experiment fixtures that are not start states

The seven **start states** moved to `configs/saves/skyemu/<game>/` on 2026-09-19,
where `configs/roms.yaml` and `configs/starts.yaml` point at them. Read that
directory's README for how each was made and what check it passed.

What is left here is the one state that is a *measurement fixture* rather than an
opening: `emerald/probe.*`, the Littleroot house. Emerald's real opening is the
moving van, which is five tiles wide with boxes on three sides — you cannot walk
far enough in it to measure anything, so the address finder uses this second
state instead. It is not something a run starts from.
