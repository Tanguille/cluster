# FileFlows size safety

`main-size-safety.json` contains the Main flow size-safety correction, applied
on 2026-10-18 as revision 21, based on the previous live revision 20.
The existing Dolby Vision script must remain installed.

The current flow rejects oversized encodes but finishes with the rejected
temporary file selected. FileFlows then records that temporary size as the final
size, even though it keeps the smaller original on disk. All eight most recent
larger results checked still had their exact original sizes on disk, including
Trading Places (21,852,929,806 bytes, reported as 28,909,676,800).

Of 570 historical larger results, one executed Replace Original: Westworld
S02E09 on 2026-08-06. It grew by 4,866,326 bytes after passing the first size
comparison and running the Dolby Vision repair. This needs a final size gate.

## Flow changes

- Add the native **Original File** node, with no outgoing connection.
- Connect outputs 2 (equal) and 3 (larger) of the existing **File Size Compare**
  to Original File. Connect output 2 of **Video Duration Compare** there too,
  so rejected encodes report the retained file's size.
- Route both Dolby Vision script outputs through another **File Size Compare**
  immediately before Replace Original. Only output 1 (smaller) reaches
  Replace Original; outputs 2 and 3 reach Original File.

Encoder, quality, cropping, languages, hardlink handling, and notifications
retain their current settings. The export changes three existing nodes' outgoing
connections and adds two native nodes.

## Applying and checking

The change was applied offline with the app stopped, preserving the flow uid,
library assignments, and revision history. The database and original flow export
are backed up in the configuration volume at
`/app/Data/Backups/size-safety-20261018T112728Z/`.
Before reusing this export, compare it with the latest live flow to preserve any
subsequent changes. The FFmpeg Builder Executor's error connection targets
`f8747db6-db11-4eae-81c4-ddd4e3911110`, which no part in this export carries;
the executor is not one of the edited nodes, so the dangling edge predates
revision 21. Reuse a fresh native export rather than hand-patching that uid.

FileFlows' database JSON uses a different boolean representation than its
portable export. Export-only `ReadOnly: false` flags on the two new nodes were
removed after startup exposed a deserialization error; the flags are optional.

Local graph checks passed for both Dolby Vision outputs, smaller/equal/larger
results before and after repair (10 combinations), and duration rejection.
After saving, verify with a test copy in FileFlows that an oversized encode
finishes on Original File, reports its original size, and does not execute
Replace Original. Verify that a smaller encode still passes the duration and
Dolby Vision checks and reaches replacement. The worker refreshed its
configuration successfully at 13:29:03 CEST and started another queued file
with the new configuration at 13:29:06. A complete encode through the new
size-check branches has not yet been observed.
Historical final-size records are not repaired by changing the flow.

The public API returned 404 and no browser was connected. The user authorized
the backed-up offline configuration edit and brief restart. FileFlows documents
that API access must be enabled and licensed.

References: [File Size Compare](https://fileflows.com/docs/plugins/basic-nodes/file-size-compare),
[Original File](https://fileflows.com/docs/plugins/basic-nodes/original-file),
[API](https://fileflows.com/docs/api/).
