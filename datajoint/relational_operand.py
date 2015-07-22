import numpy as np
import abc
import re
from collections import OrderedDict
from copy import copy
import logging
from . import config
from . import key as PRIMARY_KEY
from . import DataJointError
from .blob import unpack

logger = logging.getLogger(__name__)


class RelationalOperand(metaclass=abc.ABCMeta):
    """
    RelationalOperand implements relational algebra and fetch methods.
    RelationalOperand objects reference other relation objects linked by operators.
    The leaves of this tree of objects are base relations.
    When fetching data from the database, this tree of objects is compiled into an SQL expression.
    It is a mixin class that provides relational operators, iteration, and fetch capability.
    RelationalOperand operators are: restrict, pro, and join.
    """

    _restrictions = None

    @property
    def restrictions(self):
        return [] if self._restrictions is None else self._restrictions

    @property
    def primary_key(self):
        return self.heading.primary_key

    # --------- abstract properties -----------

    @property
    @abc.abstractmethod
    def connection(self):
        """
        :return: a datajoint.Connection object
        """
        pass

    @property
    @abc.abstractmethod
    def from_clause(self):
        """
        :return: a string containing the FROM clause of the SQL SELECT statement
        """
        pass

    @property
    @abc.abstractmethod
    def heading(self):
        """
        :return: a valid datajoint.Heading object
        """
        pass

    # --------- relational operators -----------

    def __mul__(self, other):
        """
        relational join
        """
        return Join(self, other)

    def __mod__(self, attributes=None):
        """
        relational projection operator.  See RelationalOperand.project
        """
        return self.project(*attributes)

    def project(self, *attributes, **renamed_attributes):
        """
        Relational projection operator.
        :param attributes: a list of attribute names to be included in the result.
        :return: a new relation with selected fields
        Primary key attributes are always selected and cannot be excluded.
        Therefore obj.project() produces a relation with only the primary key attributes.
        If attributes includes the string '*', all attributes are selected.
        Each attribute can only be used once in attributes or renamed_attributes.  Therefore, the projected
        relation cannot have more attributes than the original relation.
        """
        return Projection(self, *attributes, **renamed_attributes)

    def aggregate(self, group, *attributes, **renamed_attributes):
        """
        Relational aggregation operator

        :param group:  relation whose tuples can be used in aggregation operators
        :param attributes:
        :param renamed_attributes:
        :return: a relation representing the aggregation/projection operator result
        """
        if not isinstance(group, RelationalOperand):
            raise DataJointError('The second argument must be a relation')
        ret = Projection(Join(self, group, left=True), *attributes, **renamed_attributes)
        ret.heading.set_primary_key(self.primary_key)

    def _restrict(self, restriction):
        """
        in-place relational restriction or semijoin
        """
        if restriction is not None:
            if self._restrictions is None:
                self._restrictions = []
            if isinstance(restriction, list):
                self._restrictions.extend(restriction)
            else:
                self._restrictions.append(restriction)
        return self

    def __iand__(self, restriction):
        """
        in-place relational restriction or semijoin
        """
        return self._restrict(restriction)

    def __and__(self, restriction):
        """
        relational restriction or semijoin
        """
        ret = copy(self)
        ret._restrictions = list(ret.restrictions)  # copy restriction list
        ret &= restriction
        return ret

    def __isub__(self, restriction):
        """
        in-place antijoin (inverted restriction)
        """
        self &= Not(restriction)
        return self

    def __sub__(self, restriction):
        """
        inverted restriction aka antijoin
        """
        return self & Not(restriction)

    # ------ data retrieval methods -----------

    def make_select(self, attribute_spec=None):
        if attribute_spec is None:
            attribute_spec = self.heading.as_sql
        return 'SELECT %s FROM %s%s' % (attribute_spec, self.from_clause, self.where_clause)

    def __len__(self):
        """
        number of tuples in the relation.  This also takes care of the truth value
        """
        cur = self.connection.query(self.make_select('count(*)'))
        return cur.fetchone()[0]

    def __contains__(self, item):
        """
        "item in relation" is equivalent to "len(relation & item)>0"
        """
        return len(self & item) > 0

    def __call__(self, *args, **kwargs):
        """
        calling a relation is equivalent to fetching from it
        """
        return self.fetch(*args, **kwargs)

    def cursor(self, offset=0, limit=None, order_by=None, descending=False, as_dict=False):
        """
        Return query cursor.
        See Relation.fetch() for input description.
        :return: cursor to the query
        """
        if offset and limit is None:
            raise DataJointError('limit is required when offset is set')
        sql = self.make_select()
        if order_by is not None:
            sql += ' ORDER BY ' + ', '.join(order_by)
            if descending:
                sql += ' DESC'
        if limit is not None:
            sql += ' LIMIT %d' % limit
            if offset:
                sql += ' OFFSET %d' % offset
        logger.debug(sql)
        return self.connection.query(sql, as_dict=as_dict)

    def __repr__(self):
        limit = config['display.limit']
        width = config['display.width']
        rel = self.project(*self.heading.non_blobs)  # project out blobs
        template = '%%-%d.%ds' % (width, width)
        columns = rel.heading.names
        repr_string = ' '.join([template % column for column in columns]) + '\n'
        repr_string += ' '.join(['+' + '-' * (width - 2) + '+' for _ in columns]) + '\n'
        for tup in rel.fetch(limit=limit):
            repr_string += ' '.join([template % column for column in tup]) + '\n'
        if len(self) > limit:
            repr_string += '...\n'
        repr_string += ' (%d tuples)\n' % len(self)
        return repr_string

    def fetch1(self):
        """
        This version of fetch is called when self is expected to contain exactly one tuple.
        :return: the one tuple in the relation in the form of a dict
        """
        heading = self.heading
        cur = self.cursor(as_dict=True)
        ret = cur.fetchone()
        if not ret or cur.fetchone():
            raise DataJointError('fetch1 should only be used for relations with exactly one tuple')
        return OrderedDict((name, unpack(ret[name]) if heading[name].is_blob else ret[name])
                           for name in self.heading.names)

    def fetch(self, offset=0, limit=None, order_by=None, descending=False, as_dict=False):
        """
        fetches the relation from the database table into an np.array and unpacks blob attributes.
        :param offset: the number of tuples to skip in the returned result
        :param limit: the maximum number of tuples to return
        :param order_by: the list of attributes to order the results. No ordering should be assumed if order_by=None.
        :param descending: the list of attributes to order the results
        :param as_dict: returns a list of dictionaries instead of a record array
        :return: the contents of the relation in the form of a structured numpy.array
        """
        cur = self.cursor(offset=offset, limit=limit, order_by=order_by,
                          descending=descending, as_dict=as_dict)
        heading = self.heading
        if as_dict:
            ret = [OrderedDict((name, unpack(d[name]) if heading[name].is_blob else d[name])
                               for name in self.heading.names)
                   for d in cur.fetchall()]
        else:
            ret = np.array(list(cur.fetchall()), dtype=heading.as_dtype)
            for blob_name in heading.blobs:
                ret[blob_name] = list(map(unpack, ret[blob_name]))
        return ret

    def values(self, offset=0, limit=None, order_by=None, descending=False, as_dict=False):
        """
        Iterator that returns the contents of the database.
        """
        cur = self.cursor(offset=offset, limit=limit, order_by=order_by,
                          descending=descending, as_dict=as_dict)
        heading = self.heading
        do_unpack = tuple(h in heading.blobs for h in heading.names)
        values = cur.fetchone()
        while values:
            if as_dict:
                yield OrderedDict(
                    (field_name, unpack(values[field_name])) if up
                    else (field_name, values[field_name])
                    for field_name, up in zip(heading.names, do_unpack))
            else:
                yield tuple(unpack(value) if up else value for up, value in zip(do_unpack, values))
            values = cur.fetchone()

    def __iter__(self):
        """
        Iterator that yields individual tuples of the current table dictionaries.
        """
        yield from self.values(as_dict=True)

    def keys(self, *args, **kwargs):
        """
        Iterator that returns primary keys.
        """
        yield from self.project().values(*args, **kwargs)

    @property
    def where_clause(self):
        """
        convert the restriction into an SQL WHERE
        """
        if not self.restrictions:
            return ''

        def make_condition(arg):
            if isinstance(arg, dict):
                conditions = ['`%s`=%s' % (k, repr(v)) for k, v in arg.items() if k in self.heading]
            elif isinstance(arg, np.void):
                conditions = ['`%s`=%s' % (k, arg[k]) for k in arg.dtype.fields]
            else:
                raise DataJointError('invalid restriction type')
            return ' AND '.join(conditions)

        conditions = []
        for r in self.restrictions:
            negate = isinstance(r, Not)
            if negate:
                r = r.restriction
            if isinstance(r, dict) or isinstance(r, np.void):
                r = make_condition(r)
            elif isinstance(r, np.ndarray) or isinstance(r, list):
                r = '(' + ') OR ('.join([make_condition(q) for q in r]) + ')'
            elif isinstance(r, RelationalOperand):
                common_attributes = ','.join([q for q in self.heading.names if q in r.heading.names])
                r = '(%s) %sin (SELECT %s FROM %s%s)' % (
                    common_attributes, "not " if negate else "",
                    common_attributes, r.from_clause, r.where_clause)
                negate = False

            assert isinstance(r, str), 'condition must be converted into a string'
            conditions.append('%s(%s)' % ('not ' if negate else '', r))

        return ' WHERE ' + ' AND '.join(conditions)

    def __getitem__(self, item):
        """
        Fetch attributes as separate outputs.
        datajoint.key is a special value that requests the entire primary key
        :return: tuple with an entry for each element of item

        Examples:
        a, b = relation['a', 'b']
        a, b, key = relation['a', 'b', datajoint.key]
        results = relation['a':'z']    # return attributes a-z as a tuple
        results = relation[:-1]   # return all but the last attribute
        """
        single_output = isinstance(item, str) or item is PRIMARY_KEY or isinstance(item, int)
        if isinstance(item, str) or item is PRIMARY_KEY:
            item = (item,)
        elif isinstance(item, int):
            item = (self.heading.names[item],)
        elif isinstance(item, slice):
            attributes = self.heading.names
            start = attributes.index(item.start) if isinstance(item.start, str) else item.start
            stop = attributes.index(item.stop) if isinstance(item.stop, str) else item.stop
            item = attributes[slice(start, stop, item.step)]
        try:
            attributes = tuple(i for i in item if i is not PRIMARY_KEY)
        except TypeError:
            raise DataJointError("Index must be a slice, a tuple, a list, a string.")
        result = self.project(*attributes).fetch()
        return_values = [
            np.ndarray(result.shape,
                       np.dtype({name: result.dtype.fields[name] for name in self.primary_key}),
                       result, 0, result.strides)
            if attribute is PRIMARY_KEY
            else result[attribute]
            for attribute in item
            ]
        return return_values[0] if single_output else return_values


class Not:
    """
    inverse restriction
    """

    def __init__(self, restriction):
        self.restriction = restriction


class Join(RelationalOperand):
    """
    Relational join
    """
    __counter = 0

    def __init__(self, arg1, arg2, left=False):
        if not isinstance(arg2, RelationalOperand):
            raise DataJointError('a relation can only be joined with another relation')
        if arg1.connection != arg2.connection:
            raise DataJointError('Cannot join relations with different database connections')
        self._arg1 = Subquery(arg1) if arg1.heading.computed else arg1
        self._arg2 = Subquery(arg1) if arg2.heading.computed else arg2
        self._restrictions = self._arg1.restrictions + self._arg2.restrictions
        self._left = left

    @property
    def connection(self):
        return self._arg1.connection

    @property
    def counter(self):
        self.__counter += 1
        return self.__counter

    @property
    def heading(self):
        return self._arg1.heading.join(self._arg2.heading, left=self._left)

    @property
    def from_clause(self):
        return '%s NATURAL %sJOIN %s' % (
            self._arg1.from_clause, "LEFT " if self._left else "", self._arg2.from_clause)


class Projection(RelationalOperand):
    def __init__(self, arg, *attributes, **renamed_attributes):
        """
        See RelationalOperand.project()
        """
        # parse attributes in the form 'sql_expression -> new_attribute'
        alias_parser = re.compile(
            '^\s*(?P<sql_expression>\S(.*\S)?)\s*->\s*(?P<alias>[a-z][a-z_0-9]*)\s*$')
        self._attributes = []
        self._renamed_attributes = renamed_attributes
        for attribute in attributes:
            alias_match = alias_parser.match(attribute)
            if alias_match:
                d = alias_match.groupdict()
                self._renamed_attributes.update({d['alias']: d['sql_expression']})
            else:
                self._attributes.append(attribute)

        # enclose original query if necessary
        if  arg.heading.computed:
            self._arg = Subquery(arg)
        else:
            self._arg = arg
            self._restrictions = arg.restrictions

    @property
    def connection(self):
        return self._arg.connection

    @property
    def heading(self):
        return self._arg.heading.project(*self._attributes, **self._renamed_attributes)

    @property
    def from_clause(self):
        return self._arg.from_clause

    def _restrict(self, restriction):
        """
        Projection is enclosed in Subquery when restricted if it has renamed attributes
        """
        if self.heading.computed:
            return Subquery(self) & restriction
        else:
            return super()._restrict(restriction)


class Subquery(RelationalOperand):
    """
    A Subquery encapsulates its argument in a SELECT statement, enabling its use as a subquery.
    The attribute list and the WHERE clause are resolved.
    """
    __counter = 0

    def __init__(self, arg):
        self._arg = arg

    @property
    def connection(self):
        return self._arg.connection

    @property
    def counter(self):
        Subquery.__counter += 1
        return Subquery.__counter

    @property
    def from_clause(self):
        return '(' + self._arg.make_select() + ') as `_s%x`' % self.counter

    @property
    def heading(self):
        return self._arg.heading.resolve()
