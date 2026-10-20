"""DB-level backstop: nobody both annotates and reviews the same image.

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION enforce_reviewer_is_not_author() RETURNS trigger AS $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM image_submissions s
                WHERE s.image_id = NEW.image_id AND s.submitted_by = NEW.reviewer_id
            ) OR EXISTS (
                SELECT 1 FROM images i
                WHERE i.id = NEW.image_id AND i.submitted_by = NEW.reviewer_id
            ) THEN
                RAISE EXCEPTION 'reviewer % authored image %', NEW.reviewer_id, NEW.image_id
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_reviews_reviewer_not_author
        BEFORE INSERT ON reviews
        FOR EACH ROW EXECUTE FUNCTION enforce_reviewer_is_not_author();
        """
    )
    op.execute(
        """
        CREATE FUNCTION enforce_author_is_not_reviewer() RETURNS trigger AS $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM reviews r
                WHERE r.image_id = NEW.image_id AND r.reviewer_id = NEW.submitted_by
            ) THEN
                RAISE EXCEPTION 'author % already reviewed image %', NEW.submitted_by, NEW.image_id
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_submissions_author_not_reviewer
        BEFORE INSERT ON image_submissions
        FOR EACH ROW EXECUTE FUNCTION enforce_author_is_not_reviewer();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_submissions_author_not_reviewer ON image_submissions")
    op.execute("DROP FUNCTION IF EXISTS enforce_author_is_not_reviewer()")
    op.execute("DROP TRIGGER IF EXISTS trg_reviews_reviewer_not_author ON reviews")
    op.execute("DROP FUNCTION IF EXISTS enforce_reviewer_is_not_author()")
