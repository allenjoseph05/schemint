"""Ground-truth generation against a real PostgreSQL database.

The oracle is independent of the system under test: it never imports
schemint's differ, classifier, or agents. Its only use of schemint is
``LiveDBSnapshotCapture``, as a catalog *reader* — never as a judge.
"""
