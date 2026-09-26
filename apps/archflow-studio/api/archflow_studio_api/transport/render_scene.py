"""Renderer-independent visualization state. Geometry is always an exact reference."""
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator

class Value(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)

Vec3=tuple[float,float,float]

class ImageRef(Value):
    runId:str=Field(min_length=1,max_length=80)
    assetSha256:str=Field(pattern=r'^[a-f0-9]{64}$')
    revisionRef:str|None=None

class Material(Value):
    id:str=Field(pattern=r'^[a-zA-Z0-9_-]{1,80}$')
    name:str=Field(min_length=1,max_length=120)
    baseColor:str=Field(pattern=r'^#[a-fA-F0-9]{6}$')
    roughness:float=Field(ge=0,le=1,default=.65)
    metallic:float=Field(ge=0,le=1,default=0)
    texture:ImageRef|None=None
    normalMap:ImageRef|None=None

class Region(Value):
    id:str=Field(pattern=r'^[a-zA-Z0-9_-]{1,80}$')
    name:str=Field(min_length=1,max_length=120)
    materialId:str
    mesh:int=Field(ge=0)
    shape:Literal['box','ellipsoid']='box'
    center:Vec3
    radius:Vec3
    geometryRevision:str=Field(pattern=r'^[a-f0-9]{64}$')
    @model_validator(mode='after')
    def positive_radius(self):
        if min(self.radius)<=0:raise ValueError('Region radii must be positive')
        return self

class Light(Value):
    id:str=Field(pattern=r'^[a-zA-Z0-9_-]{1,80}$')
    type:Literal['area','point','sun']='area'
    position:Vec3
    target:Vec3=(0,0,.5)
    intensity:float=Field(ge=0,le=100000,default=100)
    color:str=Field(pattern=r'^#[a-fA-F0-9]{6}$',default='#ffffff')
    size:float=Field(gt=0,le=1000,default=1.5)

class Environment(Value):
    color:str=Field(pattern=r'^#[a-fA-F0-9]{6}$',default='#c6d8e8')
    strength:float=Field(ge=0,le=100,default=.4)
    background:ImageRef|None=None
    environmentMap:ImageRef|None=None

class Camera(Value):
    position:Vec3
    target:Vec3
    up:Vec3=(0,0,1)
    projection:Literal['perspective','orthographic']='perspective'
    fov:float=Field(gt=1,lt=175,default=35)
    orthoScale:float=Field(gt=0,le=100000,default=1.3)
    @model_validator(mode='after')
    def nondegenerate(self):
        d=[b-a for a,b in zip(self.position,self.target)]
        cross=[d[1]*self.up[2]-d[2]*self.up[1],d[2]*self.up[0]-d[0]*self.up[2],d[0]*self.up[1]-d[1]*self.up[0]]
        if sum(x*x for x in cross)<1e-12:raise ValueError('Camera up and view direction must define a frame')
        return self

class Quality(Value):
    width:int=Field(ge=64,le=4096,default=1000)
    height:int=Field(ge=64,le=4096,default=1200)
    samples:int=Field(ge=1,le=2048,default=128)
    denoise:bool=True

class Scene(Value):
    geometryRevision:str=Field(pattern=r'^[a-f0-9]{64}$')
    materials:list[Material]=Field(min_length=1,max_length=100)
    assignments:dict[str,str]
    regions:list[Region]=Field(default_factory=list,max_length=256)
    lights:list[Light]=Field(default_factory=list,max_length=32)
    environment:Environment=Field(default_factory=Environment)
    camera:Camera
    exposure:float=Field(ge=-10,le=10,default=0)
    settings:Quality=Field(default_factory=Quality)
    @model_validator(mode='after')
    def material_references(self):
        ids=[m.id for m in self.materials]
        if len(set(ids))!=len(ids):raise ValueError('Material IDs must be unique')
        if any(m not in ids for m in self.assignments.values()) or any(r.materialId not in ids for r in self.regions):
            raise ValueError('Assignments must name a material')
        if len({r.id for r in self.regions})!=len(self.regions) or len({r.id for r in self.lights})!=len(self.lights):
            raise ValueError('Region and light IDs must be unique')
        if any(r.geometryRevision!=self.geometryRevision for r in self.regions):
            raise ValueError('Regions are unresolved on changed geometry. Reassign them explicitly.')
        return self

class SaveScene(Value):
    expectedRevision:str|None=None
    scene:Scene

class SelectGeometry(Value):
    expectedRevision:str|None=None
    exportId:str|None=None
    runId:str|None=None
    assetSha256:str|None=None

class CyclesRequest(Value):
    requestId:UUID
    sceneRevision:str
    geometryRevision:str

class MeshDrawingRequest(Value):
    geometryRevision:str
    views:list[Literal['front','side','top','isometric']]=Field(default_factory=lambda:['front','side','top','isometric'],min_length=1,max_length=4)
    scale:int=Field(ge=1,le=1000,default=5)
    featureReferences:list[str]=Field(default_factory=list,max_length=100)
