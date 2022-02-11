import gzip
import json
import os
import sys
import tarfile
import tqdm

from gadi import api_schemas, db, models
from .parsing import parse_law
from .download import fetch_toc, has_update


def _calculate_diff(previous_slugs, current_slugs):
    previous_slugs = set(previous_slugs)
    current_slugs = set(current_slugs)

    new = current_slugs - previous_slugs
    existing = previous_slugs.intersection(current_slugs)
    removed = previous_slugs - current_slugs

    # Avoid accidentally deleting all law data directories in case of errors
    if len(removed) > 250:
        raise Exception(f"Dubious number of laws to remove ({len(removed)}) - aborting")

    return existing, new, removed


def _loop_with_progress(slugs, desc):
    pbar = None
    if sys.stdout.isatty():
        pbar = tqdm.tqdm(total=len(slugs), desc=desc)
    else:
        print(desc, '-', len(slugs))

    for slug in slugs:
        yield slug
        if pbar:
            pbar.update()

    if pbar:
        pbar.close()


def _check_for_updates(slugs, check_fn):
    updated = set()

    for slug in _loop_with_progress(slugs, "Checking existing laws for updates"):
        if check_fn(slug):
            updated.add(slug)

    return updated


def _add_or_replace(slugs, add_fn):
    for slug in _loop_with_progress(slugs, "Adding new and updated laws"):
        add_fn(slug)


def _delete_removed(slugs, delete_fn):
    for slug in _loop_with_progress(slugs, "Deleting removed laws"):
        delete_fn(slug)


def download_laws(location):
    print("Fetching toc.xml")
    download_urls = fetch_toc()

    print("Loading timestamps")
    laws_on_disk = location.list_slugs_with_timestamps()
    existing, new, removed = _calculate_diff(laws_on_disk.keys(), download_urls.keys())

    updated = _check_for_updates(existing, lambda slug: has_update(download_urls[slug], laws_on_disk[slug]))
    new_or_updated = new.union(updated)

    _add_or_replace(new_or_updated, lambda slug: location.create_or_replace_law(slug, download_urls[slug]))

    _delete_removed(removed, lambda slug: location.remove_law(slug))


def _fixup_slug_duplicates(session):
    """Use gii_slug as a law's slug in case of conflicts (except for a handful cases)."""
    overrides = {
        "aeg": {"aeg_1994": "aeg", "aeg": "aeg_2"},
        "afrg": {"altfrg": "afrg", "afrg": "afrg_2"},
        "gbv": {"gbv_2011": "gbv"},
        "stvo": {"stvo_2013": "stvo"}
    }
    dupes_by_slug = db.laws_with_duplicate_slugs(session)

    for dupes in dupes_by_slug:
        slug_overrides = overrides.get(dupes[0].slug, {})
        for law in dupes:
            law.slug = slug_overrides.get(law.gii_slug, law.gii_slug)

    session.commit()


def ingest_data_from_location(session, location):
    print("Loading timestamps")
    laws_on_disk = location.list_slugs_with_timestamps()
    laws_in_db = {
        law.gii_slug: law.source_timestamp
        for law in db.all_laws_load_only_gii_slug_and_source_timestamp(session)
    }
    existing, new, removed = _calculate_diff(laws_in_db.keys(), laws_on_disk.keys())

    updated = _check_for_updates(existing, lambda slug: laws_on_disk[slug] > laws_in_db[slug])
    new_or_updated = new.union(updated)

    def add_fn(slug):
        ingest_law(session, location, slug)
        session.commit()

    _add_or_replace(new_or_updated, add_fn)
    _fixup_slug_duplicates(session)

    print("Deleting removed laws")
    db.bulk_delete_laws_by_gii_slug(session, removed)
    session.commit()


def ingest_law(session, location, gii_slug):
    law_dict = parse_law(location.xml_file_for(gii_slug))
    attachment_names = location.attachment_names(gii_slug)
    law = models.Law.from_dict(law_dict, gii_slug)
    law.attachment_names = attachment_names

    existing_law = db.find_law_by_doknr(session, law.doknr)
    if existing_law:
        session.delete(existing_law)
        session.flush()
    session.add(law)

    return law


def _write_file(filepath, content):
    with open(filepath, "w") as f:
        f.write(content + "\n")


def _write_gzipped_file(filepath, content):
    with gzip.open(filepath, "wb") as f:
        f.write((content + "\n").encode("utf-8"))


def write_all_law_json_files(session, dir_path):
    laws_path = dir_path + "/laws"
    os.makedirs(laws_path, exist_ok=True)

    all_laws = []

    for law in db.all_laws(session):
        law_api_model = api_schemas.LawAllFields.from_orm_model(law, include_contents=True)
        single_law_response = api_schemas.LawResponse(data=law_api_model)
        _write_file(f"{laws_path}/{law.slug}.json", single_law_response.json(indent=2))

        all_laws.append(law_api_model.dict())

    _write_gzipped_file(f"{dir_path}/all_laws.json.gz", json.dumps({'data': all_laws}, indent=2))


def write_law_json_file(law, dir_path):
    filepath = f"{dir_path}/{law.slug}.json"
    law_schema = api_schemas.LawAllFields.from_orm_model(law, include_contents=True)
    response = api_schemas.LawResponse(data=law_schema)
    _write_file(filepath, response.json(indent=2))


def generate_bulk_law_files(session):
    tarfilename = "all_laws.tar.gz"
    dir_path = "."

    print("Generating json files")
    write_all_law_json_files(session, dir_path)

    print("Creating tarball")
    tarfilepath = f"{dir_path}/{tarfilename}"
    with tarfile.open(tarfilepath, "w:gz") as tf:
        tf.add(dir_path + "/laws", arcname="laws")
