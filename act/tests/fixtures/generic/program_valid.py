import pulumi


class Database(pulumi.CustomResource):
    def __init__(self, name, *, engine, version, port=5432, public=False, opts=None):
        super().__init__(
            "mydb:index:Database",
            name,
            {"engine": engine, "version": version, "port": port, "public": public},
            opts,
        )


db = Database("prod-db", engine="postgres", version="15", public=False)
pulumi.export("db_name", "prod-db")
