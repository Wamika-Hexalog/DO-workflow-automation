"""Opening a database that an older version of the app made."""
import sqlalchemy as sa

from app.db import Task, make_sessionmaker


def test_an_older_database_gains_the_new_task_column(tmp_path):
    # Protects: the app opens a database made before tasks had status_since, and stamps it from then on.
    # Fails if the column isn't added: every page that lists a job's tasks then breaks.
    url = f"sqlite:///{tmp_path / 'old.db'}"
    with sa.create_engine(url).begin() as connection:
        connection.execute(sa.text(
            "CREATE TABLE tasks (id INTEGER PRIMARY KEY, shipment_id VARCHAR(32), task_key VARCHAR(60), "
            "status VARCHAR(30), note TEXT, result JSON, completed_at DATETIME, completed_by VARCHAR(120), "
            "created_at DATETIME, updated_at DATETIME)"
        ))
    session_for = make_sessionmaker(url)
    with session_for() as session:
        session.add(Task(shipment_id="job1", task_key="finance_mail", status="pending", note="", result={}))
        session.commit()
        assert session.scalars(sa.select(Task)).one().status_since is not None
