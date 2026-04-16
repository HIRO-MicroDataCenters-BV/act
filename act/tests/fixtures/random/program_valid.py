import pulumi
from pulumi_random import RandomPassword

password = RandomPassword(
    "db-password",
    length=24,
    special=True,
)

pulumi.export("password", password.result)
