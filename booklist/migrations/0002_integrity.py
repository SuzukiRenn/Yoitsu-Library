from django.db import migrations

TRACKED = {
    'work': 'id', 'sourcefieldstate': 'work_id', 'workalias': 'work_id',
    'tag': 'NULL', 'tagsource': 'NULL', 'worktag': 'work_id', 'tagsuppression': 'work_id',
    'activity': 'NULL', 'activitywork': 'work_id', 'recommendation': 'work_id', 'vote': 'work_id',
    'workmergeoperation': 'primary_work_id', 'workmergechange': 'new_work_id',
    'workmergeoperation_depends_on': 'NULL',
}


def statements():
    sql = []
    for table, work in TRACKED.items():
        for action, row in [('insert', 'NEW'), ('update', 'NEW'), ('delete', 'OLD')]:
            origin = 'NULL' if work == 'NULL' else f'{row}.{work}'
            sql.append(f'''CREATE TRIGGER track_{table}_{action} AFTER {action.upper()} ON booklist_{table}
                BEGIN INSERT INTO booklist_changeevent(table_name,record_id,work_id,action,occurred_at)
                VALUES('booklist_{table}',{row}.id,{origin},'{action}',strftime('%Y-%m-%d %H:%M:%f','now')); END;''')
    for table in ('adminauditlog', 'changeevent', 'activitytransition', 'workmergechange'):
        for action in ('UPDATE', 'DELETE'):
            sql.append(f'''CREATE TRIGGER immutable_{table}_{action.lower()} BEFORE {action} ON booklist_{table}
                BEGIN SELECT RAISE(ABORT,'append_only_record'); END;''')
    sql.append('''CREATE TRIGGER last_admin_update BEFORE UPDATE OF is_active ON booklist_admin
        WHEN OLD.is_active=1 AND NEW.is_active=0 AND (SELECT count(*) FROM booklist_admin WHERE is_active=1)<=1
        BEGIN SELECT RAISE(ABORT,'last_active_admin'); END;''')
    sql.append('''CREATE TRIGGER last_admin_delete BEFORE DELETE ON booklist_admin
        WHEN OLD.is_active=1 AND (SELECT count(*) FROM booklist_admin WHERE is_active=1)<=1
        BEGIN SELECT RAISE(ABORT,'last_active_admin'); END;''')
    for table in ('recommendation', 'vote'):
        for action in ('INSERT', 'UPDATE'):
            sql.append(f'''CREATE TRIGGER scope_{table}_{action.lower()} BEFORE {action} ON booklist_{table}
                WHEN NEW.scope='activity' AND (NEW.archived_at IS NULL) AND NOT EXISTS
                (SELECT 1 FROM booklist_activitywork aw WHERE aw.id=NEW.activity_work_id AND aw.work_id=NEW.work_id
                AND aw.activity_id=NEW.activity_id AND aw.archived_at IS NULL)
                BEGIN SELECT RAISE(ABORT,'activity_work_mismatch'); END;''')
    sql.append('''CREATE TRIGGER snapshot_references BEFORE UPDATE OF work_id,activity_id,archived_at ON booklist_activitywork
        WHEN EXISTS (SELECT 1 FROM booklist_recommendation r WHERE r.activity_work_id=OLD.id AND r.archived_at IS NULL
            AND (r.work_id<>NEW.work_id OR r.activity_id<>NEW.activity_id OR NEW.archived_at IS NOT NULL))
        OR EXISTS (SELECT 1 FROM booklist_vote v WHERE v.activity_work_id=OLD.id AND v.archived_at IS NULL
            AND (v.work_id<>NEW.work_id OR v.activity_id<>NEW.activity_id OR NEW.archived_at IS NOT NULL))
        BEGIN SELECT RAISE(ABORT,'snapshot_still_referenced'); END;''')
    for table in ('work', 'tag'):
        sql.append(f'''CREATE TRIGGER cycle_{table} BEFORE UPDATE OF merged_into_id ON booklist_{table}
            WHEN NEW.merged_into_id IS NOT NULL BEGIN
            SELECT RAISE(ABORT,'merge_cycle') WHERE EXISTS (
              WITH RECURSIVE roots(id) AS (SELECT NEW.merged_into_id UNION
                SELECT t.merged_into_id FROM booklist_{table} t JOIN roots r ON t.id=r.id WHERE t.merged_into_id IS NOT NULL)
              SELECT 1 FROM roots WHERE id=NEW.id); END;''')
    sql.append('''CREATE TRIGGER source_merge_identity BEFORE UPDATE OF merged_into_id ON booklist_work
        WHEN NEW.merged_into_id IS NOT NULL AND NEW.wenku8_book_id IS NOT NULL
        AND EXISTS (SELECT 1 FROM booklist_work target WHERE target.id=NEW.merged_into_id
          AND (target.wenku8_book_id IS NULL OR target.wenku8_book_id<>NEW.wenku8_book_id))
        BEGIN SELECT RAISE(ABORT,'source_identity_conflict'); END;''')
    return sql


SQL = statements()
REVERSE = [f'DROP TRIGGER IF EXISTS {s.split()[2]};' for s in reversed(SQL)]


class Migration(migrations.Migration):
    dependencies = [('booklist', '0001_initial')]
    operations = [migrations.RunSQL(SQL, REVERSE)]
