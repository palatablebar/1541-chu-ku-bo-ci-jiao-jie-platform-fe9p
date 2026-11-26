import pytest
from waveclerk.entry import app
from waveclerk.pipeline import execute, open_fixture, preview

ROUTE = '/api/waveclerk'


def test_preset_workbench_has_real_business_rows():
    output = app.test_client().get(ROUTE+'/overview').get_json()
    assert len(output['metrics']) == 5 and len(output['sections']) == 8
    assert len(output['rows']) >= 10
    assert output['model'] == 'Huber 稳健时长回归'


def test_scenario_reallocates_business_capacity():
    test_client = app.test_client()
    tight = test_client.post(ROUTE+'/run',json={'capacity':1}).get_json()
    wide = test_client.post(ROUTE+'/run',json={'capacity':60,'strategy':'focus'}).get_json()
    assert tight['feature_stamp'] == wide['feature_stamp']
    assert tight['rows'] != wide['rows']
    assert tight['run_id'] != wide['run_id']


def test_windows_refuse_bad_document():
    response = app.test_client().post(ROUTE+'/run',json={'records':[{}]})
    assert response.status_code == 422 and response.get_json()['code'] == 'INVALID_BATCH'


def test_exact_health_and_template_match():
    test_client = app.test_client()
    assert test_client.get('/health').get_json() == {'status':'ok','service':'waveclerk'}
    assert '出库波次交接台' in test_client.get('/').get_data(as_text=True)
    assert test_client.get('/static/desk.js').status_code == 200


def test_entered_data_matches_preset_when_identical():
    result = app.test_client().post(ROUTE+'/run',json=open_fixture())
    assert result.status_code == 200
    assert result.get_json()['run_id'] == preview()['run_id']


def test_input_order_cannot_change_disposition():
    dataset = open_fixture()
    forward = execute(dataset['records'])
    backward = execute(list(reversed(dataset['records'])))
    assert forward['run_id'] == backward['run_id']
    assert forward['rows'] == backward['rows']


def test_changed_profile_fetches_matching_stage():
    web = app.test_client()
    run = web.post(ROUTE+'/run',json={'capacity':6, 'strategy':'focus'}).get_json()
    found = web.get(ROUTE+'/stages/'+run['sections'][6]['key']+'?capacity=6&strategy=focus')
    assert found.status_code == 200
    assert found.get_json() == {**run['sections'][6],'run_id':run['run_id']}


@pytest.mark.parametrize('capacity',[0,True,61,None,'8',3.5])
def test_capacity_type_check(capacity):
    response = app.test_client().post(ROUTE+'/run',json={'capacity':capacity})
    assert response.status_code == 422


def test_reinforce_preview_endpoint_settles():
    client = app.test_client()
    response = client.post(ROUTE+'/reinforce',json={'flex_workers':8})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['flex_pool'] == 8
    assert {'机动人数','已派机动人手','机动结余','救回波次数'} <= {m['label'] for m in payload['metrics']}
    # 人手守恒与凭证字段齐全。
    assert payload['ledger']['已用机动人手'] + payload['ledger']['机动结余'] == 8
    assert payload['stamp'] and payload['batch_hash'] and payload['policy_hash']


def test_reinforce_rerun_is_identical_and_order_invariant():
    client = app.test_client()
    first = client.post(ROUTE+'/reinforce',json={'flex_workers':7}).get_json()
    second = client.post(ROUTE+'/reinforce',json={'flex_workers':7}).get_json()
    assert first['stamp'] == second['stamp'] and first['rows'] == second['rows']
    dataset = open_fixture()
    forward = client.post(ROUTE+'/reinforce',
                          json={'flex_workers':7,'records':dataset['records'],'as_of':dataset['as_of']}).get_json()
    backward = client.post(ROUTE+'/reinforce',
                           json={'flex_workers':7,'records':list(reversed(dataset['records'])),'as_of':dataset['as_of']}).get_json()
    assert forward['stamp'] == backward['stamp'] and forward['rows'] == backward['rows']


@pytest.mark.parametrize('flex',[0,31,True,None,'6',2.5])
def test_reinforce_flex_validation(flex):
    response = app.test_client().post(ROUTE+'/reinforce',json={'flex_workers':flex})
    assert response.status_code == 422 and response.get_json()['code'] == 'INVALID_BATCH'


def test_reinforce_rejects_unknown_fields_and_bad_records():
    client = app.test_client()
    bad_field = client.post(ROUTE+'/reinforce',json={'flex_workers':5,'capacity':3})
    assert bad_field.status_code == 422
    bad_record = client.post(ROUTE+'/reinforce',json={'flex_workers':5,'records':[{}]})
    assert bad_record.status_code == 422


def test_reinforce_rescues_late_wave_end_to_end():
    # 把一张晚点单放宽到 +1 即可救回的程度，验证 API 完整派人链路。
    dataset = open_fixture()
    records = [dict(row) for row in dataset['records']]
    for row in records:
        if row['key'] == 'WAV-1253':
            row['slack_min'] = 42
    payload = app.test_client().post(
        ROUTE+'/reinforce',
        json={'flex_workers':5,'records':records,'as_of':dataset['as_of']}).get_json()
    target = next(row for row in payload['rows'] if row['单号'] == 'WAV-1253')
    assert target['处置'] == '已增援' and target['增援人数'] == 1
    assert target['原本赶不上'] is True
    assert target['增援后区间上沿'] <= target['装车缓冲']
    assert payload['ledger']['已增援名单'] == ['WAV-1253']
