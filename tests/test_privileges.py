from nose.tools import assert_true, raises
import datajoint as dj
from os import environ
from . import schema

namespace = locals()


class TestUnprivileged:

    def __init__(self):
        """A connection with only SELECT privilege to djtest schemas"""
        self.connection = dj.Connection(host=environ.get('DJ_TEST_HOST', 'localhost'), user='djview', password='djview')

    @raises(dj.DataJointError)
    def test_fail_create_schema(self):
        """creating a schema with no CREATE privilege"""
        return dj.schema('forbidden_schema', namespace, connection=self.connection)

    @raises(dj.DataJointError)
    def test_insert_failure(self):
        unprivileged = dj.schema(schema.schema.database, namespace, connection=self.connection)
        unprivileged.spawn_missing_classes()
        assert_true(issubclass(Language, dj.Lookup) and len(Language()) == len(schema.Language()),
                    'failed to spawn missing classes')
        Language().insert1(('Socrates', 'Greek'))

    @raises(dj.DataJointError)
    def test_failure_to_create_table(self):
        unprivileged = dj.schema(schema.schema.database, namespace, connection=self.connection)

        @unprivileged
        class Try(dj.Manual):
            definition = """  # should not matter really
            id : int
            ---
            value : float
            """

        Try().insert1((1, 1.5))

