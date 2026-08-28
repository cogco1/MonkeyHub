import json
import math
import rhinoscriptsyntax as rs
objects = {}
counts = {}

def _register(object_id, guids):
    if guids is None: raise Exception('build failed: ' + object_id)
    if not isinstance(guids, list): guids = [guids]
    objects[object_id] = guids

rs.AddLayer('archflow')
rs.AddLayer('archflow::aedicula-ring', (163, 138, 74))
rs.AddLayer('archflow::coffers', (203, 156, 118))
rs.AddLayer('archflow::colonnade', (140, 86, 122))
rs.AddLayer('archflow::dome', (173, 135, 111))
rs.AddLayer('archflow::portico', (149, 94, 134))
rs.AddLayer('archflow::recess-ring', (193, 95, 105))
rs.AddLayer('archflow::rotunda', (76, 174, 99))
rs.AddLayer('archflow::statuary-ring', (134, 93, 95))
rs.SetDocumentUserText('archflow:program_record', 'production-geometry-program-000-af61ca3f58bf56f618c219d1a6a3808afb6412b40dcc4fc560241630b7ff9f35.json')
rs.SetDocumentUserText('archflow:project_id', 'p065-monument-derivation')
rs.SetDocumentUserText('archflow:proposal_digest', 'ccc08756031c37f195dedd8dae6bfc65f90b719ba6f72ce5fd6733ce2aed94d7')
rs.SetDocumentUserText('archflow:proposal_id', 'monument-geometry-stage-3')
rs.SetDocumentUserText('archflow:run_id', 'monument-001')

_register('aedicula-seed-object', rs.AddBox([(39.0,37.0,70.0), (41.0,37.0,70.0), (41.0,39.0,70.0), (39.0,39.0,70.0), (39.0,37.0,73.0), (41.0,37.0,73.0), (41.0,39.0,73.0), (39.0,39.0,73.0)]))
_seed = objects['aedicula-seed-object']
for _g in _seed: rs.ObjectColorSource(_g, 3)
rs.AddBlock(_seed, (24.0,38.0,0.0), 'archflow-family-aedicula-ring', False)
_guids = []
for _i in range(36):
    _guids.append(rs.InsertBlock('archflow-family-aedicula-ring', (24.0,38.0,0.0), (1,1,1), -(0.0 + _i * 10.0), (0,0,1)))
counts['aedicula-ring-object'] = 36 * len(_seed)
_register('aedicula-ring-object', _guids)
_register('beam-seed-object', rs.AddBox([(11.0,1.0,73.0), (12.4,1.0,73.0), (12.4,14.0,73.0), (11.0,14.0,73.0), (11.0,1.0,74.2), (12.4,1.0,74.2), (12.4,14.0,74.2), (11.0,14.0,74.2)]))
_seed = objects['beam-seed-object']
for _g in _seed: rs.ObjectColorSource(_g, 3)
rs.AddBlock(_seed, (0,0,0), 'archflow-family-beam-ring', False)
_guids = []
for _i in range(9):
    _guids.append(rs.InsertBlock('archflow-family-beam-ring', (3.3*_i, 0.0*_i, 0.0*_i)))
counts['beam-ring-object'] = 9 * len(_seed)
_register('beam-ring-object', _guids)
_register('cap-front-seed-object', rs.AddBox([(10.5,1.5,72.0), (13.5,1.5,72.0), (13.5,4.5,72.0), (10.5,4.5,72.0), (10.5,1.5,73.0), (13.5,1.5,73.0), (13.5,4.5,73.0), (10.5,4.5,73.0)]))
_seed = objects['cap-front-seed-object']
for _g in _seed: rs.ObjectColorSource(_g, 3)
rs.AddBlock(_seed, (0,0,0), 'archflow-family-cap-front-ring', False)
_guids = []
for _i in range(8):
    _guids.append(rs.InsertBlock('archflow-family-cap-front-ring', (3.5*_i, 0.0*_i, 0.0*_i)))
counts['cap-front-ring-object'] = 8 * len(_seed)
_register('cap-front-ring-object', _guids)
_register('cap-rear-seed-object', rs.AddBox([(10.5,10.5,72.0), (13.5,10.5,72.0), (13.5,13.5,72.0), (10.5,13.5,72.0), (10.5,10.5,73.0), (13.5,10.5,73.0), (13.5,13.5,73.0), (10.5,13.5,73.0)]))
_seed = objects['cap-rear-seed-object']
for _g in _seed: rs.ObjectColorSource(_g, 3)
rs.AddBlock(_seed, (0,0,0), 'archflow-family-cap-rear-ring', False)
_guids = []
for _i in range(8):
    _guids.append(rs.InsertBlock('archflow-family-cap-rear-ring', (3.5*_i, 0.0*_i, 0.0*_i)))
counts['cap-rear-ring-object'] = 8 * len(_seed)
_register('cap-rear-ring-object', _guids)
_register('coffer-seed-0-object', rs.AddBox([(42.37498435543818,37.2,85.0), (43.97498435543818,37.2,85.0), (43.97498435543818,38.800000000000004,85.0), (42.37498435543818,38.800000000000004,85.0), (42.37498435543818,37.2,86.6), (43.97498435543818,37.2,86.6), (43.97498435543818,38.800000000000004,86.6), (42.37498435543818,38.800000000000004,86.6)]))
_seed = objects['coffer-seed-0-object']
for _g in _seed: rs.ObjectColorSource(_g, 3)
rs.AddBlock(_seed, (24.0,38.0,0.0), 'archflow-family-coffer-ring-0', False)
_guids = []
for _i in range(28):
    _guids.append(rs.InsertBlock('archflow-family-coffer-ring-0', (24.0,38.0,0.0), (1,1,1), -(0.0 + _i * 12.857142857142858), (0,0,1)))
counts['coffer-ring-0-object'] = 28 * len(_seed)
_register('coffer-ring-0-object', _guids)
_register('coffer-seed-1-object', rs.AddBox([(42.09136866751522,37.2,87.5), (43.69136866751522,37.2,87.5), (43.69136866751522,38.800000000000004,87.5), (42.09136866751522,38.800000000000004,87.5), (42.09136866751522,37.2,89.1), (43.69136866751522,37.2,89.1), (43.69136866751522,38.800000000000004,89.1), (42.09136866751522,38.800000000000004,89.1)]))
_seed = objects['coffer-seed-1-object']
for _g in _seed: rs.ObjectColorSource(_g, 3)
rs.AddBlock(_seed, (24.0,38.0,0.0), 'archflow-family-coffer-ring-1', False)
_guids = []
for _i in range(28):
    _guids.append(rs.InsertBlock('archflow-family-coffer-ring-1', (24.0,38.0,0.0), (1,1,1), -(0.0 + _i * 12.857142857142858), (0,0,1)))
counts['coffer-ring-1-object'] = 28 * len(_seed)
_register('coffer-ring-1-object', _guids)
_register('coffer-seed-2-object', rs.AddBox([(41.478784028338914,37.2,90.0), (43.078784028338916,37.2,90.0), (43.078784028338916,38.800000000000004,90.0), (41.478784028338914,38.800000000000004,90.0), (41.478784028338914,37.2,91.6), (43.078784028338916,37.2,91.6), (43.078784028338916,38.800000000000004,91.6), (41.478784028338914,38.800000000000004,91.6)]))
_seed = objects['coffer-seed-2-object']
for _g in _seed: rs.ObjectColorSource(_g, 3)
rs.AddBlock(_seed, (24.0,38.0,0.0), 'archflow-family-coffer-ring-2', False)
_guids = []
for _i in range(28):
    _guids.append(rs.InsertBlock('archflow-family-coffer-ring-2', (24.0,38.0,0.0), (1,1,1), -(0.0 + _i * 12.857142857142858), (0,0,1)))
counts['coffer-ring-2-object'] = 28 * len(_seed)
_register('coffer-ring-2-object', _guids)
_register('coffer-seed-3-object', rs.AddBox([(40.50386699023167,37.2,92.5), (42.10386699023167,37.2,92.5), (42.10386699023167,38.800000000000004,92.5), (40.50386699023167,38.800000000000004,92.5), (40.50386699023167,37.2,94.1), (42.10386699023167,37.2,94.1), (42.10386699023167,38.800000000000004,94.1), (40.50386699023167,38.800000000000004,94.1)]))
_seed = objects['coffer-seed-3-object']
for _g in _seed: rs.ObjectColorSource(_g, 3)
rs.AddBlock(_seed, (24.0,38.0,0.0), 'archflow-family-coffer-ring-3', False)
_guids = []
for _i in range(28):
    _guids.append(rs.InsertBlock('archflow-family-coffer-ring-3', (24.0,38.0,0.0), (1,1,1), -(0.0 + _i * 12.857142857142858), (0,0,1)))
counts['coffer-ring-3-object'] = 28 * len(_seed)
_register('coffer-ring-3-object', _guids)
_register('coffer-seed-4-object', rs.AddBox([(39.10329308849007,37.2,95.0), (40.70329308849007,37.2,95.0), (40.70329308849007,38.800000000000004,95.0), (39.10329308849007,38.800000000000004,95.0), (39.10329308849007,37.2,96.6), (40.70329308849007,37.2,96.6), (40.70329308849007,38.800000000000004,96.6), (39.10329308849007,38.800000000000004,96.6)]))
_seed = objects['coffer-seed-4-object']
for _g in _seed: rs.ObjectColorSource(_g, 3)
rs.AddBlock(_seed, (24.0,38.0,0.0), 'archflow-family-coffer-ring-4', False)
_guids = []
for _i in range(28):
    _guids.append(rs.InsertBlock('archflow-family-coffer-ring-4', (24.0,38.0,0.0), (1,1,1), -(0.0 + _i * 12.857142857142858), (0,0,1)))
counts['coffer-ring-4-object'] = 28 * len(_seed)
_register('coffer-ring-4-object', _guids)
_register('col-front-seed-object', rs.AddBox([(11.0,2.0,62.0), (13.0,2.0,62.0), (13.0,4.0,62.0), (11.0,4.0,62.0), (11.0,2.0,72.0), (13.0,2.0,72.0), (13.0,4.0,72.0), (11.0,4.0,72.0)]))
_seed = objects['col-front-seed-object']
for _g in _seed: rs.ObjectColorSource(_g, 3)
rs.AddBlock(_seed, (0,0,0), 'archflow-family-col-front-ring', False)
_guids = []
for _i in range(8):
    _guids.append(rs.InsertBlock('archflow-family-col-front-ring', (3.5*_i, 0.0*_i, 0.0*_i)))
counts['col-front-ring-object'] = 8 * len(_seed)
_register('col-front-ring-object', _guids)
_register('col-rear-seed-object', rs.AddBox([(11.0,11.0,62.0), (13.0,11.0,62.0), (13.0,13.0,62.0), (11.0,13.0,62.0), (11.0,11.0,72.0), (13.0,11.0,72.0), (13.0,13.0,72.0), (11.0,13.0,72.0)]))
_seed = objects['col-rear-seed-object']
for _g in _seed: rs.ObjectColorSource(_g, 3)
rs.AddBlock(_seed, (0,0,0), 'archflow-family-col-rear-ring', False)
_guids = []
for _i in range(8):
    _guids.append(rs.InsertBlock('archflow-family-col-rear-ring', (3.5*_i, 0.0*_i, 0.0*_i)))
counts['col-rear-ring-object'] = 8 * len(_seed)
_register('col-rear-ring-object', _guids)
_rings = []
_rings.append(rs.AddPolyline([(44.0,38.0,84.0), (43.31851652578136,43.17638090205041,84.0), (41.32050807568878,48.0,84.0), (38.14213562373095,52.14213562373095,84.0), (34.0,55.32050807568877,84.0), (29.176380902050415,57.31851652578136,84.0), (24.0,58.0,84.0), (18.82361909794959,57.31851652578136,84.0), (14.000000000000004,55.32050807568878,84.0), (9.85786437626905,52.14213562373095,84.0), (6.679491924311225,48.0,84.0), (4.681483474218638,43.17638090205042,84.0), (4.0,38.0,84.0), (4.681483474218634,32.82361909794958,84.0), (6.679491924311222,28.000000000000007,84.0), (9.857864376269042,23.857864376269056,84.0), (13.999999999999991,20.67949192431123,84.0), (18.82361909794959,18.681483474218634,84.0), (23.999999999999996,18.0,84.0), (29.176380902050404,18.68148347421863,84.0), (34.0,20.67949192431123,84.0), (38.14213562373095,23.857864376269045,84.0), (41.320508075688764,27.999999999999993,84.0), (43.31851652578136,32.82361909794957,84.0), (44.0,38.0,84.0)]))
_rings.append(rs.AddPolyline([(42.7349939951952,38.0,91.0), (42.09661455532965,42.84897325583788,91.0), (40.224980739587956,47.3674969975976,91.0), (37.247641299491775,51.247641299491775,91.0), (33.3674969975976,54.22498073958795,91.0), (28.84897325583788,56.09661455532965,91.0), (24.0,56.7349939951952,91.0), (19.15102674416212,56.09661455532965,91.0), (14.632503002402405,54.224980739587956,91.0), (10.75235870050823,51.247641299491775,91.0), (7.775019260412044,47.3674969975976,91.0), (5.903385444670349,42.84897325583788,91.0), (5.265006004804803,38.0,91.0), (5.903385444670345,33.15102674416212,91.0), (7.775019260412044,28.63250300240241,91.0), (10.752358700508221,24.752358700508236,91.0), (14.632503002402393,21.775019260412048,91.0), (19.15102674416212,19.903385444670345,91.0), (23.999999999999996,19.265006004804803,91.0), (28.848973255837873,19.903385444670345,91.0), (33.3674969975976,21.775019260412048,91.0), (37.24764129949177,24.752358700508225,91.0), (40.22498073958795,28.632503002402395,91.0), (42.09661455532965,33.1510267441621,91.0), (42.7349939951952,38.0,91.0)]))
_rings.append(rs.AddPolyline([(40.0,38.0,96.0), (39.45481322062509,42.14110472164033,96.0), (37.85640646055102,46.0,96.0), (35.31370849898476,49.31370849898476,96.0), (32.0,51.856406460551014,96.0), (28.141104721640332,53.45481322062509,96.0), (24.0,54.0,96.0), (19.858895278359668,53.45481322062509,96.0), (16.000000000000004,51.85640646055102,96.0), (12.68629150101524,49.31370849898476,96.0), (10.14359353944898,46.0,96.0), (8.545186779374909,42.14110472164034,96.0), (8.0,38.0,96.0), (8.545186779374907,33.85889527835967,96.0), (10.143593539448979,30.000000000000004,96.0), (12.686291501015234,26.686291501015248,96.0), (15.999999999999993,24.143593539448986,96.0), (19.858895278359668,22.54518677937491,96.0), (23.999999999999996,22.0,96.0), (28.141104721640325,22.545186779374905,96.0), (32.0,24.143593539448982,96.0), (35.31370849898476,26.686291501015237,96.0), (37.856406460551014,29.999999999999993,96.0), (39.45481322062509,33.858895278359654,96.0), (40.0,38.0,96.0)]))
_rings.append(rs.AddPolyline([(36.0,38.0,100.0), (35.59110991546882,41.105828541230245,100.0), (34.392304845413264,44.0,100.0), (32.48528137423857,46.48528137423857,100.0), (30.0,48.392304845413264,100.0), (27.10582854123025,49.59110991546882,100.0), (24.0,50.0,100.0), (20.894171458769755,49.59110991546882,100.0), (18.000000000000004,48.392304845413264,100.0), (15.514718625761432,46.48528137423857,100.0), (13.607695154586738,44.0,100.0), (12.408890084531185,41.10582854123025,100.0), (12.000000000000004,38.0,100.0), (12.408890084531183,34.894171458769755,100.0), (13.607695154586738,32.00000000000001,100.0), (15.514718625761427,29.51471862576144,100.0), (17.999999999999996,27.607695154586743,100.0), (20.894171458769755,26.408890084531183,100.0), (23.999999999999996,26.000000000000004,100.0), (27.10582854123024,26.40889008453118,100.0), (30.0,27.60769515458674,100.0), (32.48528137423857,29.514718625761432,100.0), (34.39230484541326,31.999999999999996,100.0), (35.59110991546881,34.89417145876974,100.0), (36.0,38.0,100.0)]))
_rings.append(rs.AddPolyline([(31.35119037979564,38.0,102.6), (31.100704641812353,39.90262807446555,102.6), (30.3663176169588,41.67559518989782,102.6), (29.19807656734681,43.19807656734681,102.6), (27.67559518989782,44.366317616958796,102.6), (25.902628074465543,45.10070464181236,102.6), (24.0,45.35119037979564,102.6), (22.097371925534457,45.10070464181236,102.6), (20.32440481010218,44.3663176169588,102.6), (18.80192343265319,43.19807656734681,102.6), (17.6336823830412,41.67559518989782,102.6), (16.899295358187647,39.90262807446555,102.6), (16.64880962020436,38.0,102.6), (16.899295358187647,36.09737192553445,102.6), (17.633682383041197,34.32440481010218,102.6), (18.80192343265319,32.8019234326532,102.6), (20.324404810102177,31.6336823830412,102.6), (22.097371925534457,30.899295358187647,102.6), (24.0,30.64880962020436,102.6), (25.90262807446554,30.899295358187647,102.6), (27.67559518989782,31.6336823830412,102.6), (29.198076567346806,32.80192343265319,102.6), (30.366317616958796,34.32440481010218,102.6), (31.10070464181235,36.097371925534446,102.6), (31.35119037979564,38.0,102.6)]))
_rings.append(rs.AddPolyline([(24.8,38.0,104.0), (24.772740661031253,38.20705523608201,104.0), (24.69282032302755,38.4,104.0), (24.565685424949237,38.56568542494924,104.0), (24.4,38.692820323027554,104.0), (24.207055236082017,38.77274066103126,104.0), (24.0,38.8,104.0), (23.792944763917983,38.77274066103126,104.0), (23.6,38.692820323027554,104.0), (23.434314575050763,38.56568542494924,104.0), (23.30717967697245,38.4,104.0), (23.227259338968747,38.20705523608202,104.0), (23.2,38.0,104.0), (23.227259338968747,37.79294476391798,104.0), (23.30717967697245,37.6,104.0), (23.434314575050763,37.43431457505076,104.0), (23.6,37.307179676972446,104.0), (23.792944763917983,37.22725933896874,104.0), (24.0,37.2,104.0), (24.207055236082017,37.22725933896874,104.0), (24.4,37.307179676972446,104.0), (24.565685424949237,37.43431457505076,104.0), (24.69282032302755,37.6,104.0), (24.772740661031253,37.79294476391798,104.0), (24.8,38.0,104.0)]))
_srf = rs.AddLoftSrf(_rings)
rs.CapPlanarHoles(_srf[0])
_register('dome-inner-object', _srf)
rs.DeleteObjects(_rings)
_rings = []
_rings.append(rs.AddPolyline([(47.0,38.0,84.0), (46.216294004648574,43.95283803735798,84.0), (43.918584287042094,49.5,84.0), (40.2634559672906,54.2634559672906,84.0), (35.5,57.91858428704209,84.0), (29.952838037357978,60.216294004648574,84.0), (24.0,61.0,84.0), (18.047161962642026,60.216294004648574,84.0), (12.500000000000005,57.918584287042094,84.0), (7.736544032709407,54.2634559672906,84.0), (4.081415712957909,49.5,84.0), (1.7837059953514327,43.952838037357985,84.0), (1.0,38.0,84.0), (1.7837059953514292,32.04716196264202,84.0), (4.081415712957906,26.500000000000007,84.0), (7.736544032709396,21.736544032709418,84.0), (12.49999999999999,18.081415712957916,84.0), (18.047161962642026,15.78370599535143,84.0), (23.999999999999996,15.0,84.0), (29.952838037357967,15.783705995351426,84.0), (35.5,18.081415712957913,84.0), (40.26345596729059,21.736544032709403,84.0), (43.91858428704208,26.49999999999999,84.0), (46.21629400464857,32.047161962642,84.0), (47.0,38.0,84.0)]))
_rings.append(rs.AddPolyline([(45.54524309447447,38.0,92.05), (44.8111067386291,43.576319244213565,92.05), (42.65872785052615,48.772621547237236,92.05), (39.23478749441554,53.23478749441554,92.05), (34.772621547237236,56.65872785052615,92.05), (29.576319244213565,58.8111067386291,92.05), (24.0,59.54524309447447,92.05), (18.42368075578644,58.8111067386291,92.05), (13.227378452762768,56.65872785052615,92.05), (8.765212505584465,53.23478749441554,92.05), (5.341272149473852,48.772621547237236,92.05), (3.1888932613709002,43.57631924421357,92.05), (2.4547569055255245,38.0,92.05), (3.1888932613709002,32.423680755786435,92.05), (5.341272149473852,27.227378452762768,92.05), (8.765212505584454,22.765212505584472,92.05), (13.227378452762753,19.34127214947386,92.05), (18.42368075578644,17.1888932613709,92.05), (23.999999999999996,16.454756905525525,92.05), (29.576319244213554,17.188893261370897,92.05), (34.772621547237236,19.341272149473856,92.05), (39.234787494415535,22.765212505584458,92.05), (42.65872785052614,27.227378452762753,92.05), (44.81110673862909,32.42368075578642,92.05), (45.54524309447447,38.0,92.05)]))
_rings.append(rs.AddPolyline([(42.400000000000006,38.0,97.8), (41.77303520371886,42.762270429886385,97.8), (39.93486742963367,47.2,97.8), (37.01076477383248,51.01076477383248,97.8), (33.2,53.93486742963367,97.8), (28.76227042988638,55.77303520371886,97.8), (24.0,56.400000000000006,97.8), (19.237729570113622,55.77303520371886,97.8), (14.800000000000002,53.93486742963367,97.8), (10.989235226167525,51.01076477383248,97.8), (8.065132570366327,47.2,97.8), (6.226964796281145,42.762270429886385,97.8), (5.599999999999998,38.0,97.8), (6.226964796281141,33.237729570113615,97.8), (8.065132570366323,28.800000000000004,97.8), (10.989235226167517,24.98923522616753,97.8), (14.79999999999999,22.06513257036633,97.8), (19.237729570113622,20.22696479628114,97.8), (23.999999999999996,19.599999999999998,97.8), (28.762270429886375,20.226964796281138,97.8), (33.2,22.06513257036633,97.8), (37.01076477383247,24.989235226167523,97.8), (39.93486742963367,28.79999999999999,97.8), (41.773035203718855,33.2377295701136,97.8), (42.400000000000006,38.0,97.8)]))
_rings.append(rs.AddPolyline([(37.8,38.0,102.4), (37.329776402789136,41.571702822414785,102.4), (35.95115057222525,44.9,102.4), (33.75807358037436,47.75807358037436,102.4), (30.9,49.95115057222525,102.4), (27.571702822414785,51.329776402789136,102.4), (24.0,51.8,102.4), (20.428297177585215,51.329776402789136,102.4), (17.100000000000005,49.95115057222525,102.4), (14.241926419625647,47.75807358037436,102.4), (12.048849427774748,44.9,102.4), (10.670223597210862,41.57170282241479,102.4), (10.200000000000003,38.0,102.4), (10.67022359721086,34.428297177585215,102.4), (12.048849427774746,31.100000000000005,102.4), (14.24192641962564,28.24192641962565,102.4), (17.099999999999994,26.04884942777475,102.4), (20.428297177585215,24.67022359721086,102.4), (23.999999999999996,24.200000000000003,102.4), (27.571702822414778,24.670223597210857,102.4), (30.9,26.048849427774748,102.4), (33.75807358037435,28.241926419625642,102.4), (35.951150572225245,31.099999999999994,102.4), (37.329776402789136,34.4282971775852,102.4), (37.8,38.0,102.4)]))
_rings.append(rs.AddPolyline([(32.453868936764984,38.0,105.39), (32.165810338084206,40.18802228563538,105.39), (31.32126525950262,42.226934468382495,105.39), (29.97778805244883,43.97778805244883,105.39), (28.226934468382495,45.32126525950262,105.39), (26.188022285635377,46.165810338084206,105.39), (24.0,46.453868936764984,105.39), (21.811977714364623,46.165810338084206,105.39), (19.773065531617508,45.32126525950262,105.39), (18.02221194755117,43.97778805244883,105.39), (16.67873474049738,42.226934468382495,105.39), (15.834189661915795,40.18802228563538,105.39), (15.546131063235014,38.0,105.39), (15.834189661915794,35.81197771436462,105.39), (16.67873474049738,33.77306553161751,105.39), (18.022211947551167,32.02221194755117,105.39), (19.773065531617505,30.678734740497383,105.39), (21.811977714364623,29.834189661915794,105.39), (24.0,29.546131063235016,105.39), (26.188022285635373,29.834189661915794,105.39), (28.226934468382495,30.678734740497383,105.39), (29.97778805244883,32.02221194755117,105.39), (31.321265259502617,33.773065531617505,105.39), (32.165810338084206,35.811977714364616,105.39), (32.453868936764984,38.0,105.39)]))
_rings.append(rs.AddPolyline([(25.0,38.0,107.0), (24.96592582628907,38.25881904510252,107.0), (24.866025403784437,38.5,107.0), (24.707106781186546,38.707106781186546,107.0), (24.5,38.86602540378444,107.0), (24.25881904510252,38.965925826289066,107.0), (24.0,39.0,107.0), (23.74118095489748,38.965925826289066,107.0), (23.5,38.86602540378444,107.0), (23.292893218813454,38.707106781186546,107.0), (23.133974596215563,38.5,107.0), (23.03407417371093,38.25881904510252,107.0), (23.0,38.0,107.0), (23.03407417371093,37.74118095489748,107.0), (23.133974596215563,37.5,107.0), (23.292893218813454,37.292893218813454,107.0), (23.5,37.13397459621556,107.0), (23.74118095489748,37.034074173710934,107.0), (24.0,37.0,107.0), (24.25881904510252,37.034074173710934,107.0), (24.5,37.13397459621556,107.0), (24.707106781186546,37.292893218813454,107.0), (24.866025403784437,37.5,107.0), (24.96592582628907,37.74118095489748,107.0), (25.0,38.0,107.0)]))
_srf = rs.AddLoftSrf(_rings)
rs.CapPlanarHoles(_srf[0])
_register('dome-outer-object', _srf)
rs.DeleteObjects(_rings)
_register('door-tool-object', rs.AddBox([(22.0,13.0,62.0), (26.0,13.0,62.0), (26.0,18.0,62.0), (22.0,18.0,62.0), (22.0,13.0,70.0), (26.0,13.0,70.0), (26.0,18.0,70.0), (22.0,18.0,70.0)]))
_c0 = rs.AddCircle(rs.PlaneFromNormal((24.0,38.0,62.0), (0.0,0.0,23.0)), 20.0)
_c1 = rs.AddCircle(rs.PlaneFromNormal((24.0,38.0,85.0), (0.0,0.0,23.0)), 20.0)
_srf = rs.AddLoftSrf([_c0, _c1])
rs.CapPlanarHoles(_srf[0])
_register('drum-inner-object', _srf)
rs.DeleteObjects([_c0, _c1])
_c0 = rs.AddCircle(rs.PlaneFromNormal((24.0,38.0,62.0), (0.0,0.0,22.0)), 23.0)
_c1 = rs.AddCircle(rs.PlaneFromNormal((24.0,38.0,84.0), (0.0,0.0,22.0)), 23.0)
_srf = rs.AddLoftSrf([_c0, _c1])
rs.CapPlanarHoles(_srf[0])
_register('drum-outer-object', _srf)
rs.DeleteObjects([_c0, _c1])
_ins = [[rs.CopyObject(_g) for _g in objects['door-tool-object']], [rs.CopyObject(_g) for _g in objects['drum-inner-object']], [rs.CopyObject(_g) for _g in objects['drum-outer-object']]]
_res = rs.BooleanDifference(_ins[2], _ins[0] + _ins[1], True)
_register('drum-wall-object', _res)
_c0 = rs.AddCircle(rs.PlaneFromNormal((24.0,38.0,103.0), (0.0,0.0,5.0)), 4.0)
_c1 = rs.AddCircle(rs.PlaneFromNormal((24.0,38.0,108.0), (0.0,0.0,5.0)), 4.0)
_srf = rs.AddLoftSrf([_c0, _c1])
rs.CapPlanarHoles(_srf[0])
_register('oculus-tool-object', _srf)
rs.DeleteObjects([_c0, _c1])
_ins = [[rs.CopyObject(_g) for _g in objects['dome-inner-object']], [rs.CopyObject(_g) for _g in objects['dome-outer-object']], [rs.CopyObject(_g) for _g in objects['oculus-tool-object']]]
_res = rs.BooleanDifference(_ins[1], _ins[0] + _ins[2], True)
_register('dome-shell-object', _res)
_register('plinth-object', rs.AddBox([(1.0,0.0,60.0), (48.0,0.0,60.0), (48.0,62.0,60.0), (1.0,62.0,60.0), (1.0,0.0,62.0), (48.0,0.0,62.0), (48.0,62.0,62.0), (1.0,62.0,62.0)]))
_register('portico-mass-object', rs.AddBox([(10.0,0.0,74.0), (38.0,0.0,74.0), (38.0,15.0,74.0), (10.0,15.0,74.0), (10.0,0.0,76.0), (38.0,0.0,76.0), (38.0,15.0,76.0), (10.0,15.0,76.0)]))
_register('recess-seed-object', rs.AddBox([(40.0,36.0,63.0), (43.0,36.0,63.0), (43.0,39.0,63.0), (40.0,39.0,63.0), (40.0,36.0,69.0), (43.0,36.0,69.0), (43.0,39.0,69.0), (40.0,39.0,69.0)]))
_seed = objects['recess-seed-object']
for _g in _seed: rs.ObjectColorSource(_g, 3)
rs.AddBlock(_seed, (24.0,38.0,0.0), 'archflow-family-recess-ring', False)
_guids = []
for _i in range(15):
    _guids.append(rs.InsertBlock('archflow-family-recess-ring', (24.0,38.0,0.0), (1,1,1), -(0.0 + _i * 24.0), (0,0,1)))
counts['recess-ring-object'] = 15 * len(_seed)
_register('recess-ring-object', _guids)
_register('statuary-seed-object', rs.AddBox([(46.5,37.5,84.0), (47.5,37.5,84.0), (47.5,38.5,84.0), (46.5,38.5,84.0), (46.5,37.5,87.0), (47.5,37.5,87.0), (47.5,38.5,87.0), (46.5,38.5,87.0)]))
_seed = objects['statuary-seed-object']
for _g in _seed: rs.ObjectColorSource(_g, 3)
rs.AddBlock(_seed, (24.0,38.0,0.0), 'archflow-family-statuary-ring', False)
_guids = []
for _i in range(72):
    _guids.append(rs.InsertBlock('archflow-family-statuary-ring', (24.0,38.0,0.0), (1,1,1), -(0.0 + _i * 5.0), (0,0,1)))
counts['statuary-ring-object'] = 72 * len(_seed)
_register('statuary-ring-object', _guids)

_physical = ['aedicula-ring-object', 'beam-ring-object', 'cap-front-ring-object', 'cap-rear-ring-object', 'coffer-ring-0-object', 'coffer-ring-1-object', 'coffer-ring-2-object', 'coffer-ring-3-object', 'coffer-ring-4-object', 'col-front-ring-object', 'col-rear-ring-object', 'dome-shell-object', 'drum-wall-object', 'plinth-object', 'portico-mass-object', 'recess-ring-object', 'statuary-ring-object']
for _oid, _guids in objects.items():
    if _oid not in _physical:
        rs.DeleteObjects(_guids)
_semantic_table = json.loads('{"aedicula-ring-object": {"layer": "archflow::aedicula-ring", "name": "aedicula-ring-object", "user_text": {"archflow:bindings": "aedicula-binding", "archflow:commitments": "commitment:preserve-monument-envelope", "archflow:component": "aedicula-ring", "archflow:evidence": "brief-claim:claim.occupancy", "archflow:producer_op": "aedicula-ring"}}, "beam-ring-object": {"layer": "archflow::colonnade", "name": "beam-ring-object", "user_text": {"archflow:bindings": "colonnade-binding", "archflow:commitments": "commitment:preserve-monument-envelope", "archflow:component": "colonnade", "archflow:evidence": "brief-claim:claim.occupancy", "archflow:producer_op": "beam-ring"}}, "cap-front-ring-object": {"layer": "archflow::colonnade", "name": "cap-front-ring-object", "user_text": {"archflow:bindings": "colonnade-binding", "archflow:commitments": "commitment:preserve-monument-envelope", "archflow:component": "colonnade", "archflow:evidence": "brief-claim:claim.occupancy", "archflow:producer_op": "cap-front-ring"}}, "cap-rear-ring-object": {"layer": "archflow::colonnade", "name": "cap-rear-ring-object", "user_text": {"archflow:bindings": "colonnade-binding", "archflow:commitments": "commitment:preserve-monument-envelope", "archflow:component": "colonnade", "archflow:evidence": "brief-claim:claim.occupancy", "archflow:producer_op": "cap-rear-ring"}}, "coffer-ring-0-object": {"layer": "archflow::coffers", "name": "coffer-ring-0-object", "user_text": {"archflow:bindings": "coffer-binding", "archflow:commitments": "commitment:preserve-monument-envelope", "archflow:component": "coffers", "archflow:evidence": "brief-claim:claim.occupancy", "archflow:producer_op": "coffer-ring-0"}}, "coffer-ring-1-object": {"layer": "archflow::coffers", "name": "coffer-ring-1-object", "user_text": {"archflow:bindings": "coffer-binding", "archflow:commitments": "commitment:preserve-monument-envelope", "archflow:component": "coffers", "archflow:evidence": "brief-claim:claim.occupancy", "archflow:producer_op": "coffer-ring-1"}}, "coffer-ring-2-object": {"layer": "archflow::coffers", "name": "coffer-ring-2-object", "user_text": {"archflow:bindings": "coffer-binding", "archflow:commitments": "commitment:preserve-monument-envelope", "archflow:component": "coffers", "archflow:evidence": "brief-claim:claim.occupancy", "archflow:producer_op": "coffer-ring-2"}}, "coffer-ring-3-object": {"layer": "archflow::coffers", "name": "coffer-ring-3-object", "user_text": {"archflow:bindings": "coffer-binding", "archflow:commitments": "commitment:preserve-monument-envelope", "archflow:component": "coffers", "archflow:evidence": "brief-claim:claim.occupancy", "archflow:producer_op": "coffer-ring-3"}}, "coffer-ring-4-object": {"layer": "archflow::coffers", "name": "coffer-ring-4-object", "user_text": {"archflow:bindings": "coffer-binding", "archflow:commitments": "commitment:preserve-monument-envelope", "archflow:component": "coffers", "archflow:evidence": "brief-claim:claim.occupancy", "archflow:producer_op": "coffer-ring-4"}}, "col-front-ring-object": {"layer": "archflow::colonnade", "name": "col-front-ring-object", "user_text": {"archflow:bindings": "colonnade-binding", "archflow:commitments": "commitment:preserve-monument-envelope", "archflow:component": "colonnade", "archflow:evidence": "brief-claim:claim.occupancy", "archflow:producer_op": "col-front-ring"}}, "col-rear-ring-object": {"layer": "archflow::colonnade", "name": "col-rear-ring-object", "user_text": {"archflow:bindings": "colonnade-binding", "archflow:commitments": "commitment:preserve-monument-envelope", "archflow:component": "colonnade", "archflow:evidence": "brief-claim:claim.occupancy", "archflow:producer_op": "col-rear-ring"}}, "dome-shell-object": {"layer": "archflow::dome", "name": "dome-shell-object", "user_text": {"archflow:bindings": "dome-binding", "archflow:commitments": "commitment:preserve-monument-envelope", "archflow:component": "dome", "archflow:evidence": "brief-claim:claim.occupancy,project://p065-monument-derivation/runs/monument-001/records/selected-spatial-option-9649fd95b181010717a2549b02a64733150bbc69060ec7d56cb3ea0d5c2ce966.json", "archflow:producer_op": "dome-shell"}}, "drum-wall-object": {"layer": "archflow::rotunda", "name": "drum-wall-object", "user_text": {"archflow:bindings": "rotunda-binding", "archflow:commitments": "commitment:preserve-monument-envelope", "archflow:component": "rotunda", "archflow:evidence": "brief-claim:claim.occupancy,project://p065-monument-derivation/runs/monument-001/records/selected-spatial-option-9649fd95b181010717a2549b02a64733150bbc69060ec7d56cb3ea0d5c2ce966.json", "archflow:producer_op": "drum-wall"}}, "plinth-object": {"layer": "archflow::rotunda", "name": "plinth-object", "user_text": {"archflow:bindings": "rotunda-binding", "archflow:commitments": "commitment:preserve-monument-envelope", "archflow:component": "rotunda", "archflow:evidence": "brief-claim:claim.occupancy,project://p065-monument-derivation/runs/monument-001/records/selected-spatial-option-9649fd95b181010717a2549b02a64733150bbc69060ec7d56cb3ea0d5c2ce966.json", "archflow:producer_op": "plinth"}}, "portico-mass-object": {"layer": "archflow::portico", "name": "portico-mass-object", "user_text": {"archflow:bindings": "portico-binding", "archflow:commitments": "commitment:preserve-monument-envelope", "archflow:component": "portico", "archflow:evidence": "brief-claim:claim.occupancy,project://p065-monument-derivation/runs/monument-001/records/selected-spatial-option-9649fd95b181010717a2549b02a64733150bbc69060ec7d56cb3ea0d5c2ce966.json", "archflow:producer_op": "portico-mass"}}, "recess-ring-object": {"layer": "archflow::recess-ring", "name": "recess-ring-object", "user_text": {"archflow:bindings": "recess-binding", "archflow:commitments": "commitment:preserve-monument-envelope", "archflow:component": "recess-ring", "archflow:evidence": "brief-claim:claim.occupancy", "archflow:producer_op": "recess-ring"}}, "statuary-ring-object": {"layer": "archflow::statuary-ring", "name": "statuary-ring-object", "user_text": {"archflow:bindings": "statuary-binding", "archflow:commitments": "commitment:preserve-monument-envelope", "archflow:component": "statuary-ring", "archflow:evidence": "brief-claim:claim.occupancy", "archflow:producer_op": "statuary-ring"}}}')
_semantics = {}
measures = {}
for _oid in _physical:
    _guids = objects.get(_oid) or []
    if not _guids:
        measures[_oid] = None
        _semantics[_oid] = None
        continue
    _meta = _semantic_table.get(_oid, {})
    for _g in _guids:
        rs.ObjectName(_g, _oid)
        if _meta.get('layer'): rs.ObjectLayer(_g, _meta['layer'])
        for _k in sorted(_meta.get('user_text', {})):
            rs.SetUserText(_g, _k, _meta['user_text'][_k])
    _first = _guids[0]
    _keys = rs.GetUserText(_first) or []
    _semantics[_oid] = {
        'name': rs.ObjectName(_first),
        'layer': rs.ObjectLayer(_first),
        'user_text': {_k: rs.GetUserText(_first, _k) for _k in _keys},
    }
    _bb = rs.BoundingBox(_guids)
    _vol = 0.0
    for _g in _guids:
        try:
            _v = rs.SurfaceVolume(_g)
            if _v: _vol += _v[0]
        except Exception:
            pass
    measures[_oid] = {
        'bbox_min': [_bb[0].X, _bb[0].Z, _bb[0].Y],
        'bbox_max': [_bb[6].X, _bb[6].Z, _bb[6].Y],
        'volume': _vol,
        'brep_count': counts.get(_oid, len(_guids)),
    }
_blocks = {}
for _bn in (rs.BlockNames() or []):
    if _bn.startswith('archflow-family-'):
        _blocks[_bn] = rs.BlockInstanceCount(_bn)
print('CAD_MEASURES=' + json.dumps(measures))
print('SEMANTICS=' + json.dumps({'objects': _semantics, 'blocks': _blocks}))