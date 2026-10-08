"""The SQLite store behind memai, one module per concern.

One store is one WAL-mode file holding memory rows, an FTS5 index, edit
history, a relations graph, and the node/edge tables behind type='diagram'
memories together under one set of ACID transactions, so there is nothing
that can desync from the metadata on a hard-kill. One such file is a
PROJECT: a home directory holds any number of them and names the one every
connect() opens by default.

Retrieval is FTS5 BM25 keyword search. It only widens the candidate set --
semantic judgment is left to the calling agent, which reads the candidates
back and decides relevance itself.
"""
