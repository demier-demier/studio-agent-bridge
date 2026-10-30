// Translational DEM: original stiffness, damping, friction, radius and fixed dt.
// No sleeping, particle deletion, adaptive coarsening or skipped neighbours.
struct Params {
    count: u32, table: u32, capacity: u32, bodies: u32,
    shape: vec4<u32>, low: vec4<f32>, bounds: vec4<f32>,
    material: vec4<f32>, // radius, mass, dt, cell width
    contact: vec4<f32>, // stiffness, damping, friction, unused
};
struct Body { center: vec4<f32>, velocity: vec4<f32>, omega: vec4<f32> };
@group(0) @binding(0) var<uniform> cfg: Params;
@group(0) @binding(1) var<storage,read> x: array<f32>;
@group(0) @binding(2) var<storage,read> v: array<f32>;
@group(0) @binding(3) var<storage,read_write> next_x: array<f32>;
@group(0) @binding(4) var<storage,read_write> next_v: array<f32>;
@group(0) @binding(5) var<storage,read_write> counts: array<atomic<u32>>;
@group(0) @binding(6) var<storage,read_write> indices: array<u32>;
@group(0) @binding(7) var<storage,read> bodies: array<Body>;
// status[0]=overflow, [1]=out of grid, [2]=nonfinite, [3]=contacts.
// Float atomic sums are implemented by compare/exchange on their bit pattern.
@group(0) @binding(8) var<storage,read_write> status: array<atomic<u32>>;
@group(0) @binding(9) var<storage,read_write> neighbours: array<u32>;
@group(0) @binding(10) var<storage,read_write> reference: array<f32>;
@group(0) @binding(11) var<storage,read_write> dispatch_args: array<u32>;
@compute @workgroup_size(1) fn prepare_dispatch(){
 let rebuild=atomicLoad(&status[388])!=0u;dispatch_args[0]=select(0u,(cfg.table+127u)/128u,rebuild);dispatch_args[1]=1u;dispatch_args[2]=1u;dispatch_args[3]=select(0u,(cfg.count+127u)/128u,rebuild);dispatch_args[4]=1u;dispatch_args[5]=1u;
}
fn read3(a: u32) -> vec3<f32> { return vec3(x[3u*a],x[3u*a+1u],x[3u*a+2u]); }
fn vel3(a: u32) -> vec3<f32> { return vec3(v[3u*a],v[3u*a+1u],v[3u*a+2u]); }
fn cell(p: vec3<f32>) -> vec3<i32> { return vec3<i32>(floor((p-cfg.low.xyz)/cfg.material.w)); }
fn valid(c: vec3<i32>) -> bool {return all(c>=vec3(0)) && all(c<vec3<i32>(cfg.shape.xyz));}
fn hash(c: vec3<i32>) -> u32 { return ((u32(c.x)*cfg.shape.y+u32(c.y))*cfg.shape.z+u32(c.z)) & (cfg.table-1u); }
fn force(n: vec3<f32>, overlap: f32, velocity: vec3<f32>) -> vec3<f32> {
    let vn=dot(velocity,n); let magnitude=max(0.0,cfg.contact.x*overlap-cfg.contact.y*vn);
    let tangent=velocity-vn*n;
    return magnitude*n-min(cfg.contact.y,cfg.contact.z*magnitude/(length(tangent)+1e-8))*tangent;
}
fn add_float(index: u32,value: f32) {
    var old=atomicLoad(&status[index]);
    loop {
        let result=atomicCompareExchangeWeak(&status[index],old,bitcast<u32>(bitcast<f32>(old)+value));
        if (result.exchanged) {break;} old=result.old_value;
    }
}
@compute @workgroup_size(128) fn clear_grid(@builtin(global_invocation_id) id: vec3<u32>) {
 if(atomicLoad(&status[388])==0u){return;}if(id.x<cfg.table){atomicStore(&counts[id.x],0u);}
}
@compute @workgroup_size(1) fn finish_grid(){if(atomicLoad(&status[388])!=0u){atomicAdd(&status[389],1u);}atomicStore(&status[388],0u);}
@compute @workgroup_size(128) fn grid(@builtin(global_invocation_id) id: vec3<u32>) {
    if(atomicLoad(&status[388])==0u){return;}
    let i=id.x; if(i>=cfg.count){return;}
    let p=read3(i); let c=cell(p);
    if(!valid(c)){atomicAdd(&status[1],1u);return;}
    let bucket=hash(c); let slot=atomicAdd(&counts[bucket],1u);
    if(slot>=cfg.capacity){atomicAdd(&status[0],1u);return;}
    indices[slot*cfg.table+bucket]=i;
}
@compute @workgroup_size(128) fn build_neighbours(@builtin(global_invocation_id) id: vec3<u32>){
 if(atomicLoad(&status[388])==0u){return;}let i=id.x;if(i>=cfg.count){return;}let p=read3(i);let c=cell(p);var found=0u;
 for(var dx=-1;dx<=1;dx++){for(var dy=-1;dy<=1;dy++){for(var dz=-1;dz<=1;dz++){
  let other=c+vec3(dx,dy,dz);if(!valid(other)){continue;}let bucket=hash(other);let size=min(atomicLoad(&counts[bucket]),cfg.capacity);
  for(var k=0u;k<size;k++){let j=indices[k*cfg.table+bucket];let delta=p-read3(j);
   if(i!=j && dot(delta,delta)<cfg.material.w*cfg.material.w){if(found<32u){neighbours[(found+1u)*cfg.count+i]=j;}else{atomicAdd(&status[0],1u);}found++;}
  }
 }}}
 neighbours[i]=min(found,32u);for(var a=0u;a<3u;a++){reference[3u*i+a]=p[a];}
}
@compute @workgroup_size(128) fn integrate(@builtin(global_invocation_id) id: vec3<u32>) {
    let i=id.x; if(i>=cfg.count){return;}
    let p=read3(i); let velocity=vel3(i); let c=cell(p); let radius=cfg.material.x;
    var f=vec3(0.0,0.0,-9.81*cfg.material.y);
    let size=neighbours[i];
    for(var k=0u;k<size;k++){
      let j=neighbours[(k+1u)*cfg.count+i];let delta=p-read3(j);let distance=length(delta);
      if(distance>1e-8 && distance<2.0*radius){f+=force(delta/distance,2.0*radius-distance,velocity-vel3(j));}
    }
    let lo=vec3(-cfg.bounds.x,-cfg.bounds.y,0.0);let hi=vec3(cfg.bounds.x,cfg.bounds.y,cfg.bounds.z);
    for(var a=0u;a<3u;a++){
        var n=vec3(0.0); n[a]=1.0;
        let overlap=lo[a]+radius-p[a];if(overlap>0.0){f+=force(n,overlap,velocity);}
        let upper=p[a]+radius-hi[a];if(upper>0.0){f+=force(-n,upper,velocity);}
    }
    for(var k=0u;k<cfg.bodies;k++){
        let body=bodies[k];let delta=p-body.center.xyz;let br=body.center.w;
        if(any(abs(delta)>vec3(radius+br))){continue;}
        let distance=length(delta);
        if(distance>1e-8 && distance<radius+br){
            let n=delta/distance;let lever=br*n;
            let reaction=force(n,radius+br-distance,velocity-body.velocity.xyz-cross(body.omega.xyz,lever));
            f+=reaction;let torque=cross(lever,-reaction);atomicAdd(&status[3],1u);
            for(var a=0u;a<3u;a++){add_float(4u+k*6u+a,-reaction[a]);add_float(4u+k*6u+3u+a,torque[a]);}
        }
    }
    let nv=velocity+cfg.material.z*f/cfg.material.y;let np=p+cfg.material.z*nv;
    // IEEE finite test without relying on nonportable shader isnan/isinf.
    if(any(abs(np)>vec3(1e10)) || any(abs(nv)>vec3(1e10)) || any(np!=np) || any(nv!=nv)){atomicAdd(&status[2],1u);}
    let anchor=vec3(reference[3u*i],reference[3u*i+1u],reference[3u*i+2u]);
    let skin=cfg.material.w-2.0*radius;
    if(length(np-anchor)>skin*0.49){atomicStore(&status[388],1u);}
    for(var a=0u;a<3u;a++){next_x[3u*i+a]=np[a];next_v[3u*i+a]=nv[a];}
}
