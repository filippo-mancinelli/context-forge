from tests.pgutil import fetchval, requires_pg, run_db, seed_org, seed_project, seed_user


@requires_pg
def test_schema_is_created_and_seeding_works(pg_database):
    async def scenario():
        org_id = await seed_org("acme")
        project_id = await seed_project(org_id, "alpha")
        await seed_user("mario", org_id, "member")
        return await fetchval("SELECT count(*) FROM projects WHERE id = $1", project_id)

    assert run_db(scenario) == 1
