"""Plain visualization projection values, no project writer or geometry authoring."""
def material_indices(mesh, mesh_index, scene):
    ids=[m['id'] for m in scene['materials']]
    base=ids.index(scene['assignments'][str(mesh_index)])
    regions=[r for r in scene['regions'] if r['mesh']==mesh_index]
    result=[]
    for face in mesh.triangles:
        p=[sum(mesh.vertices[i][axis] for i in face)/3 for axis in range(3)]
        material=base
        for region in regions:
            q=[(a-b)/r for a,b,r in zip(p,region['center'],region['radius'])]
            if (max(abs(v) for v in q)<=1 if region['shape']=='box' else sum(v*v for v in q)<=1):
                material=ids.index(region['materialId'])
        result.append(material)
    return result

def scene_plan(geometry, state):
    scene=state['scene']
    return {'binding_json':str(state['geometry']['geometryRevision']),
        'provenance_json':str(state['sceneRevision']),'length_unit':'meter','render_scene':state,
        'objects':[{'object_id':f'mesh-{i}','object_digest':state['geometry']['geometryRevision'],
         'vertices':mesh.vertices,'faces':mesh.triangles,'normals':mesh.normals,'texcoords':mesh.texcoords,
         'triangleMaterials':material_indices(mesh,i,scene),
         'semantics':{'layer':'Source geometry','visible':True,'user_text':{'archflow:object_ref':f'cad-object:mesh-{i}'}}}
         for i,mesh in enumerate(geometry.meshes)]}
