# -*- coding: utf-8 -*-
import redis
import math

from coin_mixer.constants import Config
from coin_mixer.schemas import AddressSchema as schema
from coin_mixer.models.address import Address


class DatabaseHandler(object):
    def __init__(self, test_db=True):
        self.is_test_db = test_db
        self.db = self.__get_database()

    """
    =========
    INTERFACE
    =========
    """
    def store_new_client_output_address(self, address, baseline_value,
                                        value, max_value):
        assert baseline_value is not None
        self.store_new_address(address, baseline_value, value,
                               isOnlyDecreasing=False, isOnlyIncreasing=True,
                               isForClientInput=False, isForClientOutput=True,
                               max_value=max_value)

    def store_new_decreasing_address(self, address, baseline_value, value=0,
                                     isForClientInput=False,
                                     isForClientOutput=False):
        assert baseline_value is not None
        self.store_new_address(address, baseline_value, value,
                               isOnlyDecreasing=True, isOnlyIncreasing=False,
                               isForClientInput=isForClientInput,
                               isForClientOutput=isForClientOutput)

    def store_new_increasing_address(self, address, baseline_value, value=0,
                                     isForClientInput=False,
                                     isForClientOutput=False):
        assert baseline_value is not None
        self.store_new_address(address, baseline_value, value,
                               isOnlyDecreasing=False, isOnlyIncreasing=True,
                               isForClientInput=isForClientInput,
                               isForClientOutput=isForClientOutput)

    def store_new_address(self, address, baseline_value, value=0,
                          isOnlyDecreasing=False, isOnlyIncreasing=False,
                          isForClientInput=False, isForClientOutput=False,
                          max_value=float("inf")):
        assert baseline_value is not None and baseline_value != ""
        if isOnlyDecreasing is True and isOnlyIncreasing is True:
            raise ValueError('Address cannot be increasing and decreasing')

        if isForClientInput is True and isForClientOutput is True:
            raise ValueError('Input address cannot be reused for output')

        if value < 0 or baseline_value < 0:
            raise ValueError('Value or intended value cannot be less than 0')

        addressHash = {schema.FIELD_BALANCE: value,
                       schema.FIELD_BASELINE: baseline_value,
                       schema.FIELD_MAX_VALUE: max_value}

        if len(address) != 10:
            raise ValueError('Address %s tried to be added' % address)

        pipe = self.db.pipeline()

        pipe.sadd(schema.SET_ECOSYSTEM, address)
        pipe.hmset(address, addressHash)
        pipe.incrby(schema.KEY_TOTAL_BALANCE, value)

        if isOnlyDecreasing is True:
            pipe.sadd(schema.SET_ONLY_DECREASING, address)
        elif isOnlyIncreasing is True:
            pipe.sadd(schema.SET_ONLY_INCREASING, address)

        if isForClientInput is True:
            pipe.sadd(schema.SET_CLIENT_INPUT, address)
        elif isForClientOutput is True:
            pipe.sadd(schema.SET_CLIENT_OUTPUT, address)

        pipe.execute()

    def remove_address_from_ecosystem(self, address):
        value_at_address = self.db.hget(address, schema.FIELD_BALANCE)
        is_output_address = self.db.sismember(schema.SET_CLIENT_OUTPUT,
                                              address)

        if value_at_address is None:
            value_at_address = 0
        else:
            value_at_address = int(value_at_address.decode('utf-8'))

        if math.floor(value_at_address) > 0 and is_output_address is False:
            raise ValueError('Cannot remove an internal address with coins')

        pipe = self.db.pipeline()
        pipe.delete(address)
        pipe.incrby(schema.KEY_TOTAL_BALANCE, -1*math.floor(value_at_address))
        pipe.srem(schema.SET_ONLY_DECREASING, address)
        pipe.srem(schema.SET_ONLY_INCREASING, address)
        pipe.srem(schema.SET_ECOSYSTEM, address)
        pipe.srem(schema.SET_CLIENT_INPUT, address)
        pipe.srem(schema.SET_CLIENT_OUTPUT, address)
        pipe.srem(schema.SET_COMPROMISED, address)
        pipe.execute()

    # NOTE: increase_in_value can be positve or negative
    def increment_value_at_address(self, address, increase_in_value):
        self.db.hincrby(address, schema.FIELD_BALANCE, increase_in_value)

    def total_value_in_ecosystem(self):
        return int(self.db.get(schema.KEY_TOTAL_BALANCE))

    def total_num_addresses_in_ecosystem(self):
        return int(self.db.scard(schema.SET_ECOSYSTEM))

    def total_num_compromised_addresses_in_ecosystem(self):
        return int(self.db.scard(schema.SET_COMPROMISED))

    def total_num_client_output_addresses_in_ecosystem(self):
        return int(self.db.scard(schema.SET_CLIENT_OUTPUT))

    def delete_database(self):
        if self.is_test_db is False:
            raise PermissionError('Can only clear test database')
        else:
            self.db.flushdb()

    def get_random_ecosystem_addresses(self, num_addresses):
        """
        :type num_addresses: int
        :rtype: [Address]
        """
        addresses = self.db.srandmember(schema.SET_ECOSYSTEM, num_addresses)
        addresses = list(map(lambda address: address.decode('utf-8'),
                             addresses))

        randoms = list(map(lambda address: self.get_address_data(address),
                           addresses))
        return randoms

    def mark_address_as_compromised(self, address):
        pipe = self.db.pipeline()
        pipe.sadd(schema.SET_ONLY_DECREASING, address)
        pipe.sadd(schema.SET_COMPROMISED, address)
        pipe.execute()

    def get_address_data_if_exists(self, address):
        exists = self.db.sismember(schema.SET_ECOSYSTEM, address)
        if not exists:
            return None
        else:
            return self.get_address_data(address)

    def get_address_data(self, address):
        pipe = self.db.pipeline()

        # balance
        pipe.hget(address, schema.FIELD_BALANCE)

        # baseline
        pipe.hget(address, schema.FIELD_BASELINE)

        # isOnlyDecreasing
        pipe.sismember(schema.SET_ONLY_DECREASING, address)

        # isOnlyIncreasing
        pipe.sismember(schema.SET_ONLY_INCREASING, address)

        # isForClientInput
        pipe.sismember(schema.SET_CLIENT_INPUT, address)

        # isForClientOutput
        pipe.sismember(schema.SET_CLIENT_OUTPUT, address)

        # hasBeenCompromised
        pipe.sismember(schema.SET_COMPROMISED, address)

        # maxValue
        pipe.hget(address, schema.FIELD_MAX_VALUE)

        responses = pipe.execute()
        balance, baseline, isOnlyDecreasing = responses[0:3]
        isOnlyIncreasing, isForClientInput, isForClientOutput = responses[3:6]
        hasBeenCompromised = responses[6]
        maxValue = responses[7]

        return Address(address, balance, baseline, isOnlyDecreasing,
                       isOnlyIncreasing, isForClientInput, isForClientOutput,
                       hasBeenCompromised, maxValue)

    """
    =========
    """

    def __get_database(self):
        if self.is_test_db is True:
            db_number = Config.TEST_DB_NUMBER
            db_host = Config.TEST_DB_HOST
        else:
            db_number = Config.PRODUCTION_DB_NUMBER
            db_host = Config.PRODUCTION_DB_HOST

        return redis.StrictRedis(host=db_host, port=6379, db=db_number)
