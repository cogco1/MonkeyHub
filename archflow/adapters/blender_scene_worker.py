"""Physical scene projection used by blender_worker; receives only visualization values."""
import json,math
from pathlib import Path

def linear(hex_color):
    channels=[int(hex_color[i:i+2],16)/255 for i in (1,3,5)]
    return tuple(c/12.92 if c<=.04045 else ((c+.055)/1.055)**2.4 for c in channels)

def apply(scene,plan,workspace):
    import bpy
    from mathutils import Vector
    state=plan['render_scene'];settings=state['scene'];materials=[]
    print('Render scene: assigning materials',flush=True)
    def image(ref):
        path=(workspace/(ref['assetSha256']+'.image')).resolve()
        if path.parent!=workspace:raise ValueError('Texture escaped workspace')
        value=bpy.data.images.load(str(path));value.pack();return value
    for row in settings['materials']:
        mat=bpy.data.materials.new(row['id']);mat.use_nodes=True
        bsdf=mat.node_tree.nodes.get('Principled BSDF')
        bsdf.inputs['Base Color'].default_value=(*linear(row['baseColor']),1)
        bsdf.inputs['Roughness'].default_value=row['roughness'];bsdf.inputs['Metallic'].default_value=row['metallic']
        for name in ('texture','normalMap'):
            if row[name]:
                tex=mat.node_tree.nodes.new('ShaderNodeTexImage');tex.image=image(row[name])
                if name=='texture':
                    tint=mat.node_tree.nodes.new('ShaderNodeMixRGB');tint.blend_type='MULTIPLY';tint.inputs[0].default_value=1
                    tint.inputs[2].default_value=(*linear(row['baseColor']),1)
                    mat.node_tree.links.new(tex.outputs['Color'],tint.inputs[1]);mat.node_tree.links.new(tint.outputs[0],bsdf.inputs['Base Color'])
                else:
                    tex.image.colorspace_settings.name='Non-Color'
                    normal=mat.node_tree.nodes.new('ShaderNodeNormalMap')
                    mat.node_tree.links.new(tex.outputs['Color'],normal.inputs['Color']);mat.node_tree.links.new(normal.outputs['Normal'],bsdf.inputs['Normal'])
        materials.append(mat)
    for row in plan['objects']:
        obj=bpy.data.objects[row['object_id']];mesh=obj.data
        mesh.materials.clear()
        for mat in materials:mesh.materials.append(mat)
        for face,material in zip(mesh.polygons,row['triangleMaterials'],strict=True):face.material_index=material;face.use_smooth=True
        if row.get('normals'):mesh.normals_split_custom_set_from_vertices(row['normals'])
        if row.get('texcoords'):
            uv=mesh.uv_layers.new(name='Source UV')
            for loop in mesh.loops:uv.data[loop.index].uv=row['texcoords'][loop.vertex_index]
    print('Render scene: geometry attributes ready',flush=True)
    env=settings['environment'];world=bpy.data.worlds.new('Scene environment');world.use_nodes=True;scene.world=world
    bg=world.node_tree.nodes['Background'];bg.inputs[0].default_value=(*linear(env['color']),1);bg.inputs[1].default_value=env['strength']
    if env['environmentMap']:
        tex=world.node_tree.nodes.new('ShaderNodeTexEnvironment');tex.image=image(env['environmentMap'])
        world.node_tree.links.new(tex.outputs['Color'],bg.inputs[0])
    for row in settings['lights']:
        lamp=bpy.data.lights.new(row['id'],row['type'].upper());lamp.energy=row['intensity'];lamp.color=linear(row['color'])
        if row['type']=='area':lamp.shape='SQUARE';lamp.size=row['size']
        obj=bpy.data.objects.new(row['id'],lamp);scene.collection.objects.link(obj);obj.location=row['position']
        obj.rotation_euler=(Vector(row['target'])-obj.location).to_track_quat('-Z','Y').to_euler()
    row=settings['camera'];camera=bpy.data.cameras.new('Render camera');camera.type='ORTHO' if row['projection']=='orthographic' else 'PERSP'
    camera.ortho_scale=row['orthoScale'];camera.sensor_fit='VERTICAL';camera.sensor_height=24
    camera.lens=camera.sensor_height/(2*math.tan(math.radians(row['fov'])/2))
    obj=bpy.data.objects.new('Render camera',camera);scene.collection.objects.link(obj);obj.location=row['position']
    # Build the exact requested up frame, not a fixed Z-up assumption.
    from mathutils import Matrix
    forward=(Vector(row['target'])-obj.location).normalized();right=forward.cross(Vector(row['up'])).normalized();up=right.cross(forward).normalized()
    obj.rotation_euler=Matrix((right,up,-forward)).transposed().to_euler();scene.camera=obj
    quality=settings['settings'];scene.render.engine='CYCLES';scene.cycles.samples=quality['samples'];scene.cycles.use_denoising=quality['denoise']
    scene.cycles.seed=319;scene.render.resolution_x=quality['width'];scene.render.resolution_y=quality['height'];scene.render.resolution_percentage=100
    scene.render.image_settings.file_format='PNG';scene.view_settings.view_transform='AgX';scene.view_settings.exposure=settings['exposure']
    scene.render.film_transparent=bool(env['background'])
    if env['background']:
        scene.use_nodes=True;tree=scene.node_tree;tree.nodes.clear()
        plate=tree.nodes.new('CompositorNodeImage');plate.image=image(env['background'])
        scale=tree.nodes.new('CompositorNodeScale');scale.space='RENDER_SIZE';scale.frame_method='CROP'
        layer=tree.nodes.new('CompositorNodeRLayers');over=tree.nodes.new('CompositorNodeAlphaOver');out=tree.nodes.new('CompositorNodeComposite')
        tree.links.new(plate.outputs['Image'],scale.inputs['Image']);tree.links.new(scale.outputs['Image'],over.inputs[1]);tree.links.new(layer.outputs['Image'],over.inputs[2]);tree.links.new(over.outputs['Image'],out.inputs['Image'])
    scene['render_scene']=json.dumps(state,sort_keys=True)
    print('Render scene: ready to save',flush=True)


def configure_device(scene):
    import bpy
    try:
        prefs=bpy.context.preferences.addons['cycles'].preferences;prefs.compute_device_type='OPTIX';prefs.get_devices()
        for device in prefs.devices:device.use=device.type=='OPTIX'
        if any(d.use for d in prefs.devices):scene.cycles.device='GPU'
    except (TypeError,RuntimeError):pass

def cold_render(model,plan_path,image_path):
    import bpy
    from mathutils import Vector
    from bpy_extras.object_utils import world_to_camera_view
    bpy.ops.wm.open_mainfile(filepath=str(model),load_ui=False,use_scripts=False)
    plan=json.loads(plan_path.read_text(encoding='utf-8'));state=plan['render_scene'];scene=bpy.context.scene
    configure_device(scene)
    print('Render scene: cold readback',flush=True)
    if json.loads(scene['render_scene'])!=state:raise ValueError('Saved render scene differs from pinned scene')
    maximum=0.0;assignments=[]
    for row in plan['objects']:
        obj=bpy.data.objects[row['object_id']]
        if [list(p.vertices) for p in obj.data.polygons]!=row['faces']:raise ValueError('Projection changed source topology')
        if len(obj.data.vertices)!=len(row['vertices']):raise ValueError('Projection changed source vertices')
        for actual,expected in zip(obj.data.vertices,row['vertices'],strict=True):
            maximum=max(maximum,max(abs(a-b) for a,b in zip(obj.matrix_world@actual.co,expected)))
        if maximum>1e-6:raise ValueError('Projection moved source geometry')
        actual=[p.material_index for p in obj.data.polygons]
        if actual!=row['triangleMaterials']:raise ValueError('Material assignments changed after save')
        assignments.append({str(i):actual.count(i) for i in set(actual)})
    # Compare reopened Blender values, not only the JSON stored alongside them.
    settings=state['scene'];camera=settings['camera'];native_camera=scene.camera;native_materials=[];native_lights=[]
    def near(actual,expected,label,tolerance=1e-5):
        if isinstance(expected,(list,tuple)):
            if len(actual)!=len(expected):raise ValueError(label+' length changed')
            for a,b in zip(actual,expected):near(a,b,label,tolerance)
        elif abs(actual-expected)>tolerance:raise ValueError(label+' changed after projection')
    near(list(native_camera.location),camera['position'],'Camera position')
    forward=(Vector(camera['target'])-Vector(camera['position'])).normalized()
    right=forward.cross(Vector(camera['up'])).normalized();up=right.cross(forward).normalized()
    near(list(native_camera.matrix_world.to_quaternion()@Vector((0,0,-1))),list(forward),'Camera direction')
    if native_camera.data.type!=('ORTHO' if camera['projection']=='orthographic' else 'PERSP'):raise ValueError('Camera projection changed')
    near(native_camera.data.ortho_scale,camera['orthoScale'],'Orthographic height')
    near(2*math.atan(native_camera.data.sensor_height/(2*native_camera.data.lens)),math.radians(camera['fov']),'Vertical FOV')
    aspect=settings['settings']['width']/settings['settings']['height'];projection_error=0
    for row in plan['objects']:
        # Spread samples across the real source vertex order.
        for point in row['vertices'][::max(1,len(row['vertices'])//64)]:
            delta=Vector(point)-Vector(camera['position']);depth=delta.dot(forward)
            height=camera['orthoScale'] if camera['projection']=='orthographic' else 2*depth*math.tan(math.radians(camera['fov'])/2)
            if height<=0:continue
            expected=(.5+delta.dot(right)/(height*aspect),.5+delta.dot(up)/height)
            actual=world_to_camera_view(scene,native_camera,Vector(point))
            projection_error=max(projection_error,abs(actual.x-expected[0]),abs(actual.y-expected[1]))
    if projection_error>1e-5:raise ValueError('Camera composition differs from the logical scene')
    for row in settings['materials']:
        mat=bpy.data.materials[row['id']];bsdf=mat.node_tree.nodes.get('Principled BSDF')
        color=list(bsdf.inputs['Base Color'].default_value[:3])
        near(color,linear(row['baseColor']),'Material base color')
        near(bsdf.inputs['Roughness'].default_value,row['roughness'],'Material roughness')
        near(bsdf.inputs['Metallic'].default_value,row['metallic'],'Material metallic')
        native_materials.append({'id':row['id'],'linearColor':color,'roughness':bsdf.inputs['Roughness'].default_value,'metallic':bsdf.inputs['Metallic'].default_value})
    for row in settings['lights']:
        light=bpy.data.objects[row['id']];lamp=light.data
        if lamp.type!=row['type'].upper():raise ValueError('Light type changed')
        near(list(light.location),row['position'],'Light position');near(lamp.energy,row['intensity'],'Light power')
        near(list(lamp.color),linear(row['color']),'Light color')
        if row['type']=='area':near(lamp.size,row['size'],'Light size')
        direction=(Vector(row['target'])-Vector(row['position'])).normalized()
        near(list(light.matrix_world.to_quaternion()@Vector((0,0,-1))),list(direction),'Light direction')
        native_lights.append({'id':row['id'],'type':lamp.type,'position':list(light.location),'power':lamp.energy,'linearColor':list(lamp.color)})
    bg=scene.world.node_tree.nodes['Background'];near(bg.inputs[1].default_value,settings['environment']['strength'],'Environment strength')
    near(list(bg.inputs[0].default_value[:3]),linear(settings['environment']['color']),'Environment color')
    import hashlib
    packed={hashlib.sha256(img.packed_file.data).hexdigest() for img in bpy.data.images if img.packed_file}
    expected_images=[settings['environment']['background'],settings['environment']['environmentMap'],*[m[k] for m in settings['materials'] for k in ('texture','normalMap')]]
    if any(ref and ref['assetSha256'] not in packed for ref in expected_images):raise ValueError('A scene image was not retained in the saved Blender projection')
    quality=settings['settings']
    if (scene.render.resolution_x,scene.render.resolution_y,scene.cycles.samples,scene.cycles.use_denoising)!=(quality['width'],quality['height'],quality['samples'],quality['denoise']):raise ValueError('Render quality changed')
    near(scene.view_settings.exposure,settings['exposure'],'Exposure')
    report={'geometryRevision':state['scene']['geometryRevision'],'sceneRevision':state['sceneRevision'],
            'geometryMaxErrorMeters':maximum,'topologyUnchanged':True,'materialCounts':assignments,
            'cameraWorldMatrix':[list(r) for r in scene.camera.matrix_world],'cameraProjectionMaxError':projection_error,
            'materials':native_materials,'lights':native_lights,'packedImageDigests':sorted(packed),
            'environmentStrength':bg.inputs[1].default_value,'exposure':scene.view_settings.exposure,
            'samples':scene.cycles.samples,'denoise':scene.cycles.use_denoising,'device':scene.cycles.device}
    scene.render.filepath=str(image_path);bpy.ops.render.render(write_still=True)
    (model.parent/'readback.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
