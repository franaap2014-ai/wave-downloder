"""Testes de integracao da ponte V5 sem fazer downloads externos."""
import io
import shutil
import unittest
import app as wave

class BridgeTests(unittest.TestCase):
    def setUp(self):
        wave.app.config.update(TESTING=True)
        wave.ACCESS_KEY = 'test-password-local-only'
        wave.DATABASE_URL = ''
        wave.bridge_token = None
        wave.bridge_seen_at = 0.0
        wave.active_job = None
        wave.recent_jobs.clear()
        wave.login_attempts.clear()
        with wave.lock:
            for x in wave.jobs.values():
                shutil.rmtree(x['directory'], ignore_errors=True)
            wave.jobs.clear()
        self.client = wave.app.test_client()
        response = self.client.post('/api/login', json={'password': wave.ACCESS_KEY})
        self.assertEqual(response.status_code, 200)

    def test_pair_and_heartbeat(self):
        r = self.client.post('/api/bridge/pair')
        self.assertEqual(r.status_code, 200)
        token = r.get_json()['token']
        self.assertGreater(len(token), 30)
        before = self.client.get('/api/bridge/status').get_json()
        self.assertFalse(before['connected'])
        bad = self.client.post('/api/bridge/next', headers={'X-Wave-Bridge':'incorrect'})
        self.assertEqual(bad.status_code, 403)
        ready = self.client.post('/api/bridge/next', headers={'X-Wave-Bridge':token})
        self.assertEqual(ready.status_code, 204)
        after = self.client.get('/api/bridge/status').get_json()
        self.assertTrue(after['connected'])

    def test_local_job_round_trip(self):
        token = self.client.post('/api/bridge/pair').get_json()['token']
        headers={'X-Wave-Bridge':token}
        self.client.post('/api/bridge/next',headers=headers)
        create = self.client.post('/api/jobs', json={
            'links':['https://www.youtube.com/watch?v=qq-RGFyaq0U'],
            'permission':True,'mode':'videos','platform':'youtube',
            'media_format':'mp3','quality':'192','engine':'bridge'
        })
        self.assertEqual(create.status_code, 202, create.get_json())
        job_id=create.get_json()['job']['id']
        next_ = self.client.post('/api/bridge/next',headers=headers)
        self.assertEqual(next_.status_code, 200)
        self.assertEqual(next_.get_json()['job']['id'], job_id)
        progress=self.client.post('/api/bridge/progress',headers=headers,json={'id':job_id,'progress':55})
        self.assertEqual(progress.status_code, 200)
        upload=self.client.post('/api/bridge/result',headers=headers,data={
            'id':job_id,'file':(io.BytesIO(b'test placeholder data'),'sample.mp3')
        },content_type='multipart/form-data')
        self.assertEqual(upload.status_code, 200, upload.get_json())
        done=self.client.get('/api/jobs/'+job_id).get_json()['job']
        self.assertEqual(done['status'], 'done')
        downloaded=self.client.get('/api/jobs/'+job_id+'/download')
        self.assertEqual(downloaded.status_code,200)
        self.assertEqual(downloaded.data,b'test placeholder data')

    def test_no_bridge_without_connection(self):
        response=self.client.post('/api/jobs',json={
            'links':['https://www.youtube.com/watch?v=qq-RGFyaq0U'],
            'permission':True,'mode':'videos','platform':'youtube',
            'media_format':'mp3','quality':'192','engine':'bridge'
        })
        self.assertEqual(response.status_code,409)

    def test_upload_must_match_format(self):
        token=self.client.post('/api/bridge/pair').get_json()['token']
        h={'X-Wave-Bridge':token}
        self.client.post('/api/bridge/next',headers=h)
        create=self.client.post('/api/jobs',json={
            'links':['https://www.youtube.com/watch?v=qq-RGFyaq0U'],
            'permission':True,'mode':'videos','platform':'youtube',
            'media_format':'mp3','quality':'192','engine':'bridge'
        })
        jid=create.get_json()['job']['id']
        self.client.post('/api/bridge/next',headers=h)
        response=self.client.post('/api/bridge/result',headers=h,data={
            'id':jid,'file':(io.BytesIO(b'fake'), 'bad.mp4')
        },content_type='multipart/form-data')
        self.assertEqual(response.status_code,400)
if __name__=='__main__':
    unittest.main()
