# -*- coding: utf-8 -*-
#
#  http://www.privacyidea.org
#  2018-04-16 Friedrich Weber <friedrich.weber@netknights.it>
#             Fix validation of challenge responses
#  2017-08-29 Cornelis Kölbel <cornelius.koelbel@netknights.it>
#             Initial implementation of OCRA base token
#
#
# This code is free software; you can redistribute it and/or
# modify it under the terms of the GNU AFFERO GENERAL PUBLIC LICENSE
# License as published by the Free Software Foundation; either
# version 3 of the License, or any later version.
#
# This code is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU AFFERO GENERAL PUBLIC LICENSE for more details.
#
# You should have received a copy of the GNU Affero General Public
# License along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
__doc__ = """
The OCRA token is the base OCRA functionality. Usually it is created by 
importing a CSV or PSKC file. 

This code is tested in tests/test_lib_tokens_tiqr.
"""

import logging
import hashlib

from privacyidea.api.lib.utils import getParam
from privacyidea.lib.config import get_from_config
from privacyidea.lib.tokenclass import TokenClass
from privacyidea.lib.log import log_with
from privacyidea.lib.utils import create_img, hexlify_and_unicode, to_bytes
from privacyidea.models import Challenge
from privacyidea.lib.user import get_user_from_param
from privacyidea.lib.tokens.ocra import OCRASuite, OCRA
from privacyidea.lib import _
from privacyidea.lib.decorators import check_token_locked
from privacyidea.lib.crypto import get_alphanum_str
from privacyidea.lib.policy import SCOPE, ACTION, GROUP

OCRA_DEFAULT_SUITE = "OCRA-1:HOTP-SHA1-8:QH40"

log = logging.getLogger(__name__)
optional = True
required = False


class OcraTokenClass(TokenClass):
    """
    The OCRA Token Implementation
    """

    @staticmethod
    def get_class_type():
        """
        Returns the internal token type identifier
        :return: ocra
        :rtype: basestring
        """
        return "ocra"

    @staticmethod
    def get_class_prefix():
        """
        Return the prefix, that is used as a prefix for the serial numbers.
        :return: OCRA
        :rtype: basestring
        """
        return "OCRA"

    @staticmethod
    @log_with(log)
    def get_class_info(key=None, ret='all'):
        """
        returns a subtree of the token definition

        :param key: subsection identifier
        :type key: string
        :param ret: default return value, if nothing is found
        :type ret: user defined
        :return: subsection if key exists or user defined
        :rtype: dict or scalar
        """
        res = {'type': 'ocra',
               'title': 'OCRA Token',
               'description': _('OCRA: Enroll an OCRA token.'),
               'init': {},
               'config': {},
               #'user':  ['enroll'],
               # This tokentype is enrollable in the UI for...
               'ui_enroll': [],
               'policy': {
                   SCOPE.ENROLL: {
                       ACTION.MAXTOKENUSER: {
                           'type': 'int',
                           'desc': _("The user may only have this maximum number of OCRA tokens assigned."),
                           'group': GROUP.TOKEN
                       },
                       ACTION.MAXACTIVETOKENUSER: {
                           'type': 'int',
                           'desc': _("The user may only have this maximum number of active OCRA tokens assigned."),
                           'group': GROUP.TOKEN
                       }
                   }
               },
               }

        if key:
            ret = res.get(key, {})
        else:
            if ret == 'all':
                ret = res
        return ret

    @log_with(log)
    def __init__(self, db_token):
        """
        Create a new OCRA Token object from a database object

        :param db_token: instance of the orm db object
        :type db_token: DB object
        """
        TokenClass.__init__(self, db_token)
        self.set_type(u"ocra")
        self.hKeyRequired = False

    def update(self, param):
        """
        This method is called during the initialization process.

        :param param: parameters from the token init
        :type param: dict
        :return: None
        """
        user_object = get_user_from_param(param, optional)
        if user_object:
            self.add_user(user_object)

        ocrasuite = getParam(param, "ocrasuite", default=OCRA_DEFAULT_SUITE)
        OCRASuite(ocrasuite)
        self.add_tokeninfo("ocrasuite", ocrasuite)
        TokenClass.update(self, param)

        if user_object:
            # We have to set the realms here, since the token DB object does not
            # have an ID before TokenClass.update.
            self.set_realms([user_object.realm])

    @log_with(log)
    def is_challenge_request(self, passw, user=None, options=None):
        """
        check, if the request would start a challenge
        In fact every Request that is not a response needs to start a
        challenge request.

        At the moment we do not think of other ways to trigger a challenge.

        This function is not decorated with
            @challenge_response_allowed
        as the OCRA token is always a challenge response token!

        :param passw: The PIN of the token.
        :param options: dictionary of additional request parameters

        :return: returns true or false
        """
        options = options or {}
        return self.check_pin(passw, user=user, options=options)

    def create_challenge(self, transactionid=None, options=None):
        """
        This method creates a challenge, which is submitted to the user.
        The submitted challenge will be preserved in the challenge
        database.

        If no transaction id is given, the system will create a transaction
        id and return it, so that the response can refer to this transaction.

        :param transactionid: the id of this challenge
        :param options: the request context parameters / data
        :type options: dict
        :return: tuple of (bool, message, transactionid, reply_dict)
        :rtype: tuple

        The return tuple builds up like this:
        ``bool`` if submit was successful;
        ``message`` which is displayed in the JSON response;
        additional challenge ``reply_dict``, which are displayed in the JSON challenges response.
        """
        options = options or {}
        message = 'Please answer the challenge'
        attributes = {}

        # Get ValidityTime=120s. Maybe there is a OCRAChallengeValidityTime...
        validity = int(get_from_config('DefaultChallengeValidityTime', 120))
        tokentype = self.get_tokentype().lower()
        lookup_for = tokentype.capitalize() + 'ChallengeValidityTime'
        validity = int(get_from_config(lookup_for, validity))

        # Get the OCRASUITE from the token information
        ocrasuite = self.get_tokeninfo("ocrasuite") or OCRA_DEFAULT_SUITE

        challenge = options.get("challenge")
        # TODO: we could add an additional parameter to hash the challenge
        # cleartext -> sha1
        if not challenge:
            # If no challenge is given in the Request, we create a random
            # challenge based on the OCRA-SUITE
            os = OCRASuite(ocrasuite)
            challenge = os.create_challenge()
        else:
            # Add a random challenge
            if options.get("addrandomchallenge"):
                challenge += get_alphanum_str(int(options.get(
                    "addrandomchallenge")))
            attributes["original_challenge"] = challenge
            attributes["qrcode"] = create_img(challenge)
            if options.get("hashchallenge", "").lower() == "sha256":
                challenge = hexlify_and_unicode(hashlib.sha256(to_bytes(challenge)).digest())
            elif options.get("hashchallenge", "").lower() == "sha512":
                challenge = hexlify_and_unicode(hashlib.sha512(to_bytes(challenge)).digest())
            elif options.get("hashchallenge"):
                challenge = hexlify_and_unicode(hashlib.sha1(to_bytes(challenge)).digest())


        # Create the challenge in the database
        db_challenge = Challenge(self.token.serial,
                                 transaction_id=None,
                                 challenge=challenge,
                                 data=None,
                                 session=None,
                                 validitytime=validity)
        db_challenge.save()

        attributes["challenge"] = challenge
        reply_dict = {"attributes": attributes}

        return True, message, db_challenge.transaction_id, reply_dict

    def verify_response(self, passw=None, challenge=None):
        """
        This method verifies if the *passw* is the valid OCRA response to the
        *challenge*.
        In case of success we return a value > 0

        :param passw: the password (pin+otp)
        :type passw: string
        :return: return otp_counter. If -1, challenge does not match
        :rtype: int
        """
        ocrasuite = self.get_tokeninfo("ocrasuite")
        security_object = self.token.get_otpkey()
        ocra_object = OCRA(ocrasuite, security_object=security_object)
        # TODO: We might need to add additional Signing or Counter objects
        r = ocra_object.check_response(passw, question=challenge)
        return r

    @check_token_locked
    def check_otp(self, otpval, counter=None, window=None, options=None):
        """
        This function is invoked by ``TokenClass.check_challenge_response``
        and checks if the given password matches the expected response
        for the given challenge.

        :param otpval: the password (pin + otp)
        :param counter: ignored
        :param window: ignored
        :param options: dictionary that *must* contain "challenge"
        :return: >=0 if the challenge matches, -1 otherwise
        """
        return self.verify_response(otpval, options['challenge'])

    @staticmethod
    def get_import_csv(l):
        """
        Read the list from a csv file and return a dictionary, that can be used
        to do a token_init.

        :param l: The list of the line of a csv file
        :type l: list
        :return: A dictionary of init params
        """
        params = TokenClass.get_import_csv(l)

        # Delete the otplen, if it exists. The fourth column is the ocrasuite!
        if "otplen" in params:
            del params["otplen"]

        # ocrasuite
        if len(l) >= 4:
            params["ocrasuite"] = l[3].strip()

        return params
