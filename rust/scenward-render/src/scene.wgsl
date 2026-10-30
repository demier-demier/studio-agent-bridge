struct Camera {
    vp: mat4x4<f32>, inverse_vp: mat4x4<f32>, shadow_vp: mat4x4<f32>,
    eye_radius: vec4<f32>, right: vec4<f32>, up: vec4<f32>,
    color: vec4<f32>, viewport: vec4<f32>,
};
@group(0) @binding(0) var<uniform> camera: Camera;
@group(0) @binding(1) var shadow_map: texture_depth_2d;
@group(0) @binding(2) var shadow_sampler: sampler_comparison;

fn shadow_at(p: vec3<f32>) -> f32 {
    let q = camera.shadow_vp * vec4(p, 1.0);
    let uv = vec2(q.x / q.w * 0.5 + 0.5, 0.5 - q.y / q.w * 0.5);
    if (any(uv < vec2(0.0)) || any(uv > vec2(1.0))) { return 1.0; }
    var shadow = 0.0;
    for (var x = -1; x <= 1; x++) {
        for (var y = -1; y <= 1; y++) {
            shadow += textureSampleCompareLevel(shadow_map, shadow_sampler,
                uv + vec2(f32(x), f32(y)) / 2048.0, q.z / q.w - 0.0007);
        }
    }
    return shadow / 9.0;
}
fn shade(p: vec3<f32>, n: vec3<f32>, base: vec3<f32>, rough: f32, metallic: f32) -> vec3<f32> {
    let l = normalize(vec3(-0.5, -0.4, 1.0));
    let v = normalize(camera.eye_radius.xyz - p);
    let h = normalize(l + v);
    let nl = max(dot(n,l), 0.0); let nv = max(dot(n,v), 0.001);
    let nh = max(dot(n,h), 0.0); let vh = max(dot(v,h), 0.0);
    let a = rough * rough; let a2 = a * a;
    let denom = nh * nh * (a2 - 1.0) + 1.0;
    let d = a2 / max(3.14159265 * denom * denom, 0.0001);
    let k = (rough + 1.0) * (rough + 1.0) / 8.0;
    let g = (nv / (nv * (1.0-k) + k)) * (nl / (nl * (1.0-k) + k));
    let f0 = mix(vec3(0.04), base, metallic);
    let f = f0 + (vec3(1.0)-f0) * pow(1.0-vh, 5.0);
    let spec = d * g * f / max(4.0*nv*nl, 0.001);
    let diffuse = base * (1.0-metallic) / 3.14159265;
    let key = (diffuse + spec) * nl * shadow_at(p) * vec3(3.8,3.5,3.0);
    let fill = base * max(dot(n,normalize(vec3(0.3,0.9,0.6))),0.0) * vec3(0.16,0.25,0.38);
    let ambient = base * (0.08 + 0.12 * clamp(n.z*0.5+0.5,0.0,1.0));
    let c = key + fill + ambient;
    // ACES-style filmic display transform; the target handles sRGB encoding.
    return clamp((c*(2.51*c+0.03))/(c*(2.43*c+0.59)+0.14),vec3(0.0),vec3(1.0));
}

struct SphereOut {
    @builtin(position) clip: vec4<f32>,
    @location(0) @interpolate(flat) center: vec3<f32>,
    @location(1) @interpolate(flat) id: u32,
};
@vertex fn sphere_vs(@location(0) center: vec3<f32>, @builtin(vertex_index) vertex: u32,
                     @builtin(instance_index) id: u32) -> SphereOut {
    let quad = array<vec2<f32>,6>(vec2(-1.0,-1.0), vec2(1.0,-1.0), vec2(1.0,1.0),
                                vec2(-1.0,-1.0), vec2(1.0,1.0), vec2(-1.0,1.0));
    let radius = camera.eye_radius.w;
    let distance = length(center - camera.eye_radius.xyz);
    let bound = radius * distance / sqrt(max(distance*distance-radius*radius,0.000001)) * 1.01;
    let p = center + (camera.right.xyz*quad[vertex].x + camera.up.xyz*quad[vertex].y)*bound;
    var out: SphereOut; out.clip = camera.vp * vec4(p,1.0); out.center=center; out.id=id;
    return out;
}
struct SphereFragment { @location(0) color: vec4<f32>, @builtin(frag_depth) depth: f32 };
@fragment fn sphere_fs(in: SphereOut) -> SphereFragment {
    let ndc = vec2(in.clip.x/camera.viewport.x*2.0-1.0, 1.0-in.clip.y/camera.viewport.y*2.0);
    let q = camera.inverse_vp * vec4(ndc,1.0,1.0);
    let ray = normalize(q.xyz/q.w - camera.eye_radius.xyz);
    let oc = camera.eye_radius.xyz - in.center;
    let b = dot(oc,ray); let radius = camera.eye_radius.w;
    // Closest-approach form avoids subtracting two large squared distances
    // when millimetre grains are viewed from metres away.
    let closest = oc - b*ray;
    let discriminant = radius*radius-dot(closest,closest);
    if (discriminant < 0.0) { discard; }
    let t = -b-sqrt(discriminant); if (t <= 0.0) { discard; }
    let hit = camera.eye_radius.xyz + t*ray;
    let n = normalize(hit-in.center);
    let projected = camera.vp * vec4(hit,1.0);
    var variation = 1.0;
    if (camera.color.w > 0.5) {
        let h = (in.id * 1664525u + 1013904223u);
        variation = 0.78 + 0.44*f32(h & 65535u)/65535.0;
    }
    var out: SphereFragment;
    out.depth = projected.z/projected.w;
    out.color = vec4(shade(hit,n,camera.color.xyz*variation,0.72,0.0),1.0);
    return out;
}

struct MeshIn {
    @location(0) position: vec3<f32>, @location(1) normal: vec3<f32>,
    @location(2) m0: vec4<f32>, @location(3) m1: vec4<f32>,
    @location(4) m2: vec4<f32>, @location(5) m3: vec4<f32>, @location(6) color: vec4<f32>,
};
struct MeshOut {
    @builtin(position) clip: vec4<f32>, @location(0) world: vec3<f32>,
    @location(1) normal: vec3<f32>, @location(2) color: vec4<f32>,
};
@vertex fn mesh_vs(in: MeshIn) -> MeshOut {
    let model=mat4x4(in.m0,in.m1,in.m2,in.m3);
    let world=model*vec4(in.position,1.0);
    var out: MeshOut; out.clip=camera.vp*world; out.world=world.xyz;
    out.normal=normalize((model*vec4(in.normal,0.0)).xyz); out.color=in.color; return out;
}
@vertex fn shadow_vs(in: MeshIn) -> @builtin(position) vec4<f32> {
    return camera.shadow_vp * mat4x4(in.m0,in.m1,in.m2,in.m3)*vec4(in.position,1.0);
}
@fragment fn mesh_fs(in: MeshOut) -> @location(0) vec4<f32> {
    var color=in.color.xyz; var metallic=0.72; var rough=0.28;
    if (in.color.w < 0.5) {
        let grid=abs(fract(in.world.xy*10.0-0.5)-0.5)/max(fwidth(in.world.xy*10.0),vec2(0.001));
        let line=1.0-min(min(grid.x,grid.y),1.0);
        color += vec3(0.012,0.026,0.035)*line; metallic=0.0; rough=0.85;
    }
    return vec4(shade(in.world,normalize(in.normal),color,rough,metallic),1.0);
}
