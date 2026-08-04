"""pytest session fixture: put every test under the transmission sandbox.

JI_TESTS=1 is set BEFORE any test module imports, so every send path
(reach.py email/message/connect, lib.linkedin_messaging) hard-refuses to
transmit for the whole suite. A test can never send a real message, no
matter how its browser mocks resolve.
"""
import os

os.environ["JI_TESTS"] = "1"
