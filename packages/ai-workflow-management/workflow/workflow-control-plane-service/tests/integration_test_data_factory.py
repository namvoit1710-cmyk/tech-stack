"""Integration test against live data-factory + file-service."""
import asyncio

DATA_FACTORY = "https://smdg-ai-dev-data-factory.cfapps.br10.hana.ondemand.com"
FILE_SERVICE = "https://smdg-ai-dev-file-service.cfapps.br10.hana.ondemand.com"

from app.layer4_frameworks.clients.http_data_factory_client import HttpDataFactoryClient
from app.layer4_frameworks.clients.http_file_service_client import HttpFileServiceClient

file_client = HttpFileServiceClient(FILE_SERVICE)
df_client = HttpDataFactoryClient(DATA_FACTORY)


async def main():
    # 1. Upload test CSV
    csv_data = b"url,method,status\nhttps://a.com,GET,200\nhttps://b.com,POST,404\nhttps://c.com,PUT,500\n"
    upload = file_client.upload_file(csv_data, "integration-test.csv", "text/csv")
    file_id = upload["file_id"]
    print(f"1. Uploaded CSV -> file_id: {file_id}")

    # 2. Delete row index=1 (https://b.com)
    print("\n2. Delete row index=1 (https://b.com)")
    result = await df_client.transform_rows(
        file_id=file_id,
        operations=[{"operation": "delete", "row_identifier": {"type": "index", "index": 1}}],
    )
    print(f"   success={result.get('success')}  file_id={result.get('file_id')}")
    print(f"   affected_rows={result.get('affected_rows')}  total_rows={result.get('total_rows')}")

    new_fid = result.get("file_id", "")
    assert result.get("success") is True, f"Delete failed: {result}"
    assert new_fid, "No file_id returned from delete"

    raw = file_client.download_file(new_fid)
    text = raw.decode("utf-8")
    print(f"   Content:\n{text.strip()}")
    assert "https://b.com" not in text, "Row was not deleted"
    assert "https://a.com" in text
    assert "https://c.com" in text
    print("   PASS: https://b.com deleted")

    # 3. Insert row before index=0
    print("\n3. Insert row before index=0")
    result2 = await df_client.transform_rows(
        file_id=file_id,
        operations=[{
            "operation": "insert",
            "row_identifier": {"type": "index", "index": 0},
            "data": {"url": "https://new.com", "method": "PATCH", "status": "201"},
            "position": "before",
        }],
    )
    print(f"   success={result2.get('success')}  file_id={result2.get('file_id')}")

    new_fid2 = result2.get("file_id", "")
    assert result2.get("success") is True, f"Insert failed: {result2}"
    assert new_fid2, "No file_id returned from insert"

    raw2 = file_client.download_file(new_fid2)
    text2 = raw2.decode("utf-8")
    print(f"   Content:\n{text2.strip()}")
    assert "https://new.com" in text2
    lines = text2.strip().split("\n")
    assert len(lines) == 5, f"Expected 5 lines (header+4 rows), got {len(lines)}"
    print("   PASS: row inserted")

    # 4. Multiple ops: delete index=2 + insert after index=0
    print("\n4. Multiple ops: delete + insert")
    result3 = await df_client.transform_rows(
        file_id=file_id,
        operations=[
            {"operation": "delete", "row_identifier": {"type": "index", "index": 2}},
            {
                "operation": "insert",
                "row_identifier": {"type": "index", "index": 0},
                "data": {"url": "https://multi.com", "method": "OPTIONS", "status": "204"},
                "position": "after",
            },
        ],
    )
    print(f"   success={result3.get('success')}  file_id={result3.get('file_id')}")
    assert result3.get("success") is True
    new_fid3 = result3.get("file_id", "")
    raw3 = file_client.download_file(new_fid3)
    text3 = raw3.decode("utf-8")
    print(f"   Content:\n{text3.strip()}")
    assert "https://c.com" not in text3, "Row 2 was not deleted"
    assert "https://multi.com" in text3, "Inserted row missing"
    print("   PASS: multi-ops")

    print("\n" + "=" * 50)
    print("ALL INTEGRATION TESTS PASSED")
    print("=" * 50)


asyncio.run(main())
