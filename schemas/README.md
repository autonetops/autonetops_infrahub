# Local schemas

`intent/` is a mirror of `schema-library/experimental/intent` (the
canonical home). It lives here temporarily because the schema-library
fork is not writable by this automation yet - load it with:

    infrahubctl schema load schemas/intent

Once the schema-library branch `claude/intent-network-modeling-5ih0dh`
lands, prefer loading from there and delete this mirror.
