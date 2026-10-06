import io
import json
import unittest
from unittest.mock import patch
from contentrium_cut.contract import CutError
from contentrium_cut.model_setup import community_revision


class MetadataTests(unittest.TestCase):
    def test_fixed_authenticated_provider_no_redirect_and_bounded_response(self):
        calls=[];url='https://huggingface.co/api/models/pyannote/speaker-diarization-community-1/revision/main'
        class Response(io.BytesIO):
            status=200
            def geturl(self):return url
        class Opener:
            data=json.dumps({'sha':'a'*40}).encode()
            def open(self,request,timeout):calls.append((request.full_url,request.get_header('Authorization'),timeout));return Response(self.data)
        opener=Opener();handlers=[]
        with patch('contentrium_cut.model_setup.build_opener',side_effect=lambda handler:handlers.append(handler) or opener):
            self.assertEqual(community_revision('private-token'),'a'*40)
            self.assertEqual(calls,[(url,'Bearer private-token',5)])
            self.assertIsNone(handlers[0].redirect_request(None,None,None,None,None,'https://other.test'))
            opener.data=b'x'*(512*1024+1)
            with self.assertRaises(CutError) as error:community_revision('private-token')
            self.assertNotIn('private-token',str(error.exception))
            opener.data=json.dumps({'sha':'wrong private-token'}).encode()
            with self.assertRaises(CutError):community_revision('private-token')
