"""Orthographic silhouettes and extent measurements of caller-supplied mesh geometry."""
from math import sqrt
from shapely.geometry import Polygon
from shapely.ops import unary_union
from archflow.adapters.occt_backend import OcctDrawingPolyline
from .drawing_svg import drawing_svg, render_svg_png

FRAMES={'front':((1,0,0),(0,0,1)), 'side':((0,1,0),(0,0,1)),
        'top':((1,0,0),(0,1,0)),
        'isometric':((1/sqrt(2),-1/sqrt(2),0),(1/sqrt(6),1/sqrt(6),2/sqrt(6)))}

def mesh_view(meshes, view, scale):
    right,up=FRAMES[view];all_points=[];lines=[]
    for index,mesh in enumerate(meshes):
        uv=[(sum(a*b for a,b in zip(p,right)),sum(a*b for a,b in zip(p,up))) for p in mesh.vertices]
        all_points.extend(uv);polygons=[]
        for face in mesh.triangles:
            polygon=Polygon([uv[i] for i in face])
            if polygon.area>1e-16:polygons.append(polygon)
        outline=unary_union(polygons)
        for part in list(outline.geoms) if hasattr(outline,'geoms') else [outline]:
            if part.is_empty:continue
            for ring in [part.exterior,*part.interiors]:
                lines.append(OcctDrawingPolyline(str(index),'visible',tuple(ring.coords)))
    low=[min(p[i] for p in all_points) for i in range(2)];high=[max(p[i] for p in all_points) for i in range(2)]
    width,height=[b-a for a,b in zip(low,high)]
    margin=max(.15*scale/5,.15*max(width,height));crop=(low[0]-margin,low[1]-margin,high[0]+margin,high[1]+margin)
    dims=[{'status':'resolved','id':'overall-width','label':f'{width*1000:.3f} mm','start':low,'end':[high[0],low[1]],'offsetMm':-10,'value':width},
          {'status':'resolved','id':'overall-height','label':f'{height*1000:.3f} mm','start':[high[0],low[1]],'end':high,'offsetMm':-12,'value':height}]
    svg=drawing_svg(lines,crop_uv=crop,unit='meter',scale_denominator=scale,hidden_lines=False,title=f'{view} — mesh silhouette',dimensions=dims)
    return svg,render_svg_png(svg),{'right':right,'up':up,'scale':scale,'dimensions':[
        {'id':d['id'],'reference':{'kind':'projected-extent','axis':i},'valueMeters':d['value'],'status':'resolved'} for i,d in enumerate(dims)]}
