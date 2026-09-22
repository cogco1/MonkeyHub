"""Portable visualization values. No engine, geometry writer or persistence."""
from copy import deepcopy
from dataclasses import dataclass
import math
import re


def _number(value, low, high):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"Expected a finite number in [{low}, {high}]")


def _keys(value, names):
    if not isinstance(value, dict) or set(value) != set(names.split()):
        raise ValueError("Unexpected or missing visualization fields")


def _vector(value):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError("Expected a three-dimensional vector")
    for x in value:
        _number(x, -1e9, 1e9)


def _color(value):
    if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        raise ValueError("Expected an sRGB hex color")


@dataclass(frozen=True)
class ProjectVisualizationState:
    """Validated value. Return detached copies; never expose mutable authority."""
    _value: dict

    @classmethod
    def from_dict(cls, value):
        _keys(value, "camera savedCameras materials lights environment renderSettings")
        def camera(c):
            _keys(c, "position target up projection fov orthoHeight near far")
            for name in ("position", "target", "up"):
                _vector(c[name])
            d = [b-a for a,b in zip(c['position'], c['target'])]; u=c['up']
            cross = [d[1]*u[2]-d[2]*u[1], d[2]*u[0]-d[0]*u[2], d[0]*u[1]-d[1]*u[0]]
            if sum(x*x for x in cross) < 1e-20 or c['projection'] not in ('perspective','orthographic'):
                raise ValueError("Invalid camera frame")
            _number(c['fov'], 1, 175); _number(c['orthoHeight'], 1e-6, 1e9)
            _number(c['near'], 1e-6, 1e9); _number(c['far'], c['near']+1e-6, 1e10)
        camera(value['camera'])
        if not isinstance(value['savedCameras'], list) or len(value['savedCameras']) > 32:
            raise ValueError("At most 32 saved cameras")
        for row in value['savedCameras']:
            _keys(row, 'name camera')
            if not isinstance(row['name'], str) or not 1 <= len(row['name']) <= 80:
                raise ValueError("Invalid camera name")
            camera(row['camera'])
        materials=value['materials']
        if not isinstance(materials, list) or not 1 <= len(materials) <= 128:
            raise ValueError("Invalid material collection")
        targets=set()
        for m in materials:
            _keys(m, 'objectId baseColor roughness metallic opacity')
            if not isinstance(m['objectId'],str) or not 1 <= len(m['objectId']) <= 240 or m['objectId'] in targets:
                raise ValueError('Material targets must be unique object IDs')
            targets.add(m['objectId']); _color(m['baseColor'])
            for k in ('roughness','metallic','opacity'): _number(m[k], 0, 1)
        if '*' not in targets: raise ValueError('A default material is required')
        lights=value['lights']
        if not isinstance(lights,list) or len(lights)>8: raise ValueError('At most 8 lights')
        ids=set()
        for light in lights:
            _keys(light,'id type position target color intensity width height shadow')
            if not isinstance(light['id'],str) or not 1<=len(light['id'])<=80 or light['id'] in ids:
                raise ValueError('Invalid light ID')
            ids.add(light['id'])
            if light['type'] not in ('point','directional','area') or type(light['shadow']) is not bool:
                raise ValueError('Invalid light type/shadow')
            _vector(light['position']); _vector(light['target']); _color(light['color'])
            _number(light['intensity'],0,10000)
            for k in ('width','height'): _number(light[k],.001,1e6)
        e=value['environment']; _keys(e,'background color intensity')
        _color(e['background']); _color(e['color']); _number(e['intensity'],0,10)
        r=value['renderSettings']; _keys(r,'width height exposure')
        for k in ('width','height'):
            if type(r[k]) is not int: raise ValueError('Image dimensions must be integers')
            _number(r[k],64,4096)
        _number(r['exposure'],.05,10)
        return cls(deepcopy(value))

    def to_dict(self):
        return deepcopy(self._value)


def default_visualization():
    return ProjectVisualizationState.from_dict({
        'camera': {'position':[2,-3,2], 'target':[0,0,.5], 'up':[0,0,1],
                   'projection':'perspective','fov':38,'orthoHeight':2,'near':.001,'far':10000},
        'savedCameras':[],
        'materials':[{'objectId':'*','baseColor':'#c8cdd4','roughness':.55,'metallic':0,'opacity':1}],
        'lights':[{'id':'key','type':'directional','position':[3,-4,5],'target':[0,0,0],
                   'color':'#ffffff','intensity':3,'width':2,'height':2,'shadow':True}],
        'environment':{'background':'#30343b','color':'#ffffff','intensity':.7},
        'renderSettings':{'width':1024,'height':1024,'exposure':1},
    }).to_dict()
