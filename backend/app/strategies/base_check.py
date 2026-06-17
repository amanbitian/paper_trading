"""Temporary diagnostic file - checking write/sync behavior. This file will be deleted.

Padding line to make the file larger than 662 bytes so we can confirm the
mount reflects a freshly-written file's true size rather than some cached
662-byte snapshot. Padding padding padding padding padding padding padding
padding padding padding padding padding padding padding padding padding.
"""
X = 1
