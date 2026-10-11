"""doc_versions' read side (V1 spec §2, §3.4, §5, §7): CURRENT listings,
HISTORY lists, versions_in, the display name, and listed vs readable."""
import uuid

import pytest
from fastapi import HTTPException

from server.auth import authz
from server.services import doc_versions
from server.tests import seed
from server.tests.chat_harness import member

pytestmark = pytest.mark.asyncio

C1, C2, C3, C4 = "1" * 64, "2" * 64, "3" * 64, "4" * 64


async def _content(db_conn, holder, *ids, names=None):
    for i, doc in enumerate(ids):
        name = names[i] if names else f"{doc[0]}.pdf"
        await seed.seed_doc(db_conn, doc, holder, file_name=name, state="indexed")


async def test_lists_one_row_per_document_its_current_version_sorted_by_name(db_conn):
    alice = await member(db_conn, "r-alice")
    pid = await seed.make_project(db_conn, alice.user_id, "P")
    await _content(db_conn, alice.user_id, C1, C2, C3, C4)
    x = await seed.place_doc(db_conn, pid, C1, alice.user_id, file_name="old.pdf")
    await seed.add_version(db_conn, x, C2, alice.user_id, file_name="b-report.pdf")
    await seed.place_doc(db_conn, pid, C3, alice.user_id, file_name="Z.pdf")
    await seed.place_doc(db_conn, pid, C4, alice.user_id, file_name="legacy.pdf", verified=False)
    rows = await doc_versions.list_project_documents(db_conn, pid, show_email=False)
    assert [(r["name"], r["version"], r["versions_count"], r["doc_id"]) for r in rows] == [
        ("b-report.pdf", 2, 2, C2), ("Z.pdf", 1, 1, C3)]
    assert rows[0]["id"] == x and rows[0]["state"] == "indexed"
    assert rows[0]["updated_by"]["id"] == alice.user_id


async def test_history_is_newest_first_and_hides_unverified_versions(db_conn):
    alice = await member(db_conn, "r-alice2")
    pid = await seed.make_project(db_conn, alice.user_id, "P")
    await _content(db_conn, alice.user_id, C1, C2, C3)
    x = await seed.place_doc(db_conn, pid, C1, alice.user_id)
    await seed.add_version(db_conn, x, C3, alice.user_id, current=False, verified=False)
    await seed.add_version(db_conn, x, C2, alice.user_id, note="fixed table 4")
    rows = await doc_versions.list_versions(db_conn, pid, x, show_email=False)
    assert [(r["version"], r["is_current"], r["note"]) for r in rows] == [
        (3, True, "fixed table 4"), (1, False, None)]


async def test_history_of_another_projects_document_is_404(db_conn):
    alice = await member(db_conn, "r-alice3")
    pid = await seed.make_project(db_conn, alice.user_id, "P")
    other = await seed.make_project(db_conn, alice.user_id, "Q")
    await _content(db_conn, alice.user_id, C1)
    x = await seed.place_doc(db_conn, other, C1, alice.user_id)
    for document_id in (x, str(uuid.uuid4())):
        with pytest.raises(HTTPException) as e:
            await doc_versions.list_versions(db_conn, pid, document_id, show_email=False)
        assert e.value.status_code == 404
        with pytest.raises(HTTPException):
            await doc_versions.lock_document(db_conn, pid, document_id)


async def test_versions_in_names_only_visible_projects_and_flags_current(db_conn):
    alice, bob = await member(db_conn, "r-alice4"), await member(db_conn, "r-bob4")
    p1 = await seed.make_project(db_conn, alice.user_id, "Alpha")
    p2 = await seed.make_project(db_conn, alice.user_id, "Beta")
    p3 = await seed.make_project(db_conn, bob.user_id, "Hidden")
    await _content(db_conn, alice.user_id, C1, C2)
    await seed.place_doc(db_conn, p1, C1, alice.user_id)                       # current in Alpha
    y = await seed.place_doc(db_conn, p2, C1, alice.user_id)
    await seed.add_version(db_conn, y, C2, alice.user_id, file_name="new.pdf")  # old in Beta
    await seed.place_doc(db_conn, p3, C1, alice.user_id)                       # alice isn't in Hidden
    items = await doc_versions.versions_in(db_conn, C1, alice.user_id)
    assert [(i["project"]["name"], i["version"], i["is_current"], i["current_version"],
             i["current_doc_id"], i["current_name"]) for i in items] == [
        ("Alpha", 1, True, 1, C1, "1.pdf"), ("Beta", 1, False, 2, C2, "new.pdf")]
    assert doc_versions.old_version_of(items) is None  # current somewhere: no banner
    assert doc_versions.old_version_of(items[1:])["project"]["name"] == "Beta"
    assert doc_versions.old_version_of([]) is None


async def test_versions_in_reports_the_highest_number_when_content_repeats(db_conn):
    alice = await member(db_conn, "r-alice5")
    pid = await seed.make_project(db_conn, alice.user_id, "P")
    await _content(db_conn, alice.user_id, C1, C2)
    x = await seed.place_doc(db_conn, pid, C1, alice.user_id)
    await seed.add_version(db_conn, x, C2, alice.user_id)
    await seed.add_version(db_conn, x, C1, alice.user_id, origin="restore", origin_version=1)
    [item] = await doc_versions.versions_in(db_conn, C1, alice.user_id)
    assert (item["version"], item["is_current"]) == (3, True)


async def test_display_name_is_my_entry_then_a_version_name_then_canonical(db_conn):
    alice, bob = await member(db_conn, "r-alice6"), await member(db_conn, "r-bob6")
    pid = await seed.make_project(db_conn, alice.user_id, "P", members=[(bob.user_id, "reader")])
    await _content(db_conn, alice.user_id, C1, names=["canonical.pdf"])
    await seed.place_doc(db_conn, pid, C1, alice.user_id, file_name="As filed.pdf")

    async def name_for(user_id):
        cur = await db_conn.execute(
            f"SELECT {doc_versions.display_name_sql('d', '_me')} FROM documents d "
            "LEFT JOIN library_entries _me ON _me.doc_id = d.doc_id AND _me.user_id = %s "
            "WHERE d.doc_id = %s",
            [*doc_versions.display_name_params(user_id), user_id, C1])
        return (await cur.fetchone())[0]

    assert await name_for(alice.user_id) == "canonical.pdf"   # my entry, NULL name = canonical
    assert await name_for(bob.user_id) == "As filed.pdf"      # no entry: the version's name
    await db_conn.execute("UPDATE library_entries SET file_name = 'Mine.pdf' WHERE user_id = %s",
                          (alice.user_id,))
    assert await name_for(alice.user_id) == "Mine.pdf"


async def test_an_old_version_is_readable_but_not_listed(db_conn):
    alice, bob = await member(db_conn, "r-alice7"), await member(db_conn, "r-bob7")
    pid = await seed.make_project(db_conn, alice.user_id, "P", members=[(bob.user_id, "reader")])
    await _content(db_conn, alice.user_id, C1, C2)
    x = await seed.place_doc(db_conn, pid, C1, alice.user_id)
    await seed.add_version(db_conn, x, C2, alice.user_id)

    async def where(sql, params, doc):
        cur = await db_conn.execute(f"SELECT 1 FROM documents d WHERE d.doc_id = %s AND {sql}",
                                    [doc, *params])
        return await cur.fetchone() is not None

    readable = (authz.readable_docs_where("d"), authz.readable_docs_params(bob.user_id))
    listed = (authz.listed_docs_where("d"), authz.listed_docs_params(bob.user_id))
    assert await where(*readable, C1) and not await where(*listed, C1)
    assert await where(*readable, C2) and await where(*listed, C2)
    # alice's own entry for C1 lists it for her even though it's old in the project
    assert await where(authz.listed_docs_where("d"), authz.listed_docs_params(alice.user_id), C1)


async def test_sql_placeholders_match_their_params():
    assert doc_versions.display_name_sql("d", "_me").count("%s") == len(doc_versions.display_name_params("u"))
    for only_old in (False, True):
        assert doc_versions.versions_in_sql("d", only_old=only_old).count("%s") == len(
            doc_versions.versions_in_params("u"))
    assert authz.listed_docs_where("d").count("%s") == len(authz.listed_docs_params("u"))
