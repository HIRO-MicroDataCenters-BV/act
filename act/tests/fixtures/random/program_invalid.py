import pulumi
from pulumi_random import RandomPassword

# Violation: length too short, no special characters
password = RandomPassword(
    "db-password",
    length=6,
    special=False,
)

pulumi.export("password", password.result)
