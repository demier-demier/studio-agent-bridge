// Narrow, version-pinned MuJoCo C ABI. No Python interpreter in the live loop.
#include <mujoco/mujoco.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <math.h>
typedef struct {mjModel*m;mjData*d;int feet[64];int count;int visual[4096];int visual_count;} Robot;
void* sw_robot_open(const char*path,char*error,int error_size){
 if(mj_version()!=337){snprintf(error,error_size,"MuJoCo 3.3.7 is required");return NULL;}
 Robot*r=calloc(1,sizeof(Robot));if(!r)return NULL;
 r->m=mj_loadXML(path,NULL,error,error_size);if(!r->m){free(r);return NULL;}
 if(r->m->nu!=12 || r->m->nq<19 || r->m->nv<18 || r->m->nbody<18 || r->m->njnt<13 || r->m->jnt_type[0]!=mjJNT_FREE || fabs(r->m->opt.timestep-.001)>1e-12){snprintf(error,error_size,"Expected Solo12, free base, 12 actuators, 1ms timestep");mj_deleteModel(r->m);free(r);return NULL;}
 for(int j=1;j<=12;j++){if(r->m->jnt_type[j]!=mjJNT_HINGE || r->m->jnt_qposadr[j]!=j+6 || r->m->jnt_dofadr[j]!=j+5){snprintf(error,error_size,"Unexpected Solo12 joint layout");mj_deleteModel(r->m);free(r);return NULL;}}
 r->d=mj_makeData(r->m);if(!r->d){mj_deleteModel(r->m);free(r);return NULL;}
 const char*names[]={"FL_pad","FR_pad","HL_pad","HR_pad"};
 for(int i=0;i<4;i++){int id=mj_name2id(r->m,mjOBJ_GEOM,names[i]);if(id<0 || r->m->geom_type[id]!=mjGEOM_SPHERE){snprintf(error,error_size,"Missing spherical foot pad");mj_deleteData(r->d);mj_deleteModel(r->m);free(r);return NULL;}r->feet[r->count++]=id;}
 for(int i=0;i<r->m->ngeom;i++){
  const char*name=mj_id2name(r->m,mjOBJ_GEOM,i);
  if(name && strncmp(name,"SW_GRAIN_",9)==0){
   if(r->count>=64 || r->m->geom_type[i]!=mjGEOM_SPHERE){snprintf(error,error_size,"Grain coupling requires spheres and at most 64 coupled geoms");mj_deleteData(r->d);mj_deleteModel(r->m);free(r);return NULL;}
   r->feet[r->count++]=i;
  }
  if(r->m->geom_type[i]==mjGEOM_MESH && r->m->geom_group[i]==1){
   if(r->visual_count>=4096){snprintf(error,error_size,"Too many robot visual meshes");mj_deleteData(r->d);mj_deleteModel(r->m);free(r);return NULL;}
   r->visual[r->visual_count++]=i;
  }
 }
 // Preserve free-joint defaults of the imported dynamic objects.
 r->d->qpos[0]=-.36;r->d->qpos[1]=0;r->d->qpos[2]=.0015*2.035*9.*.94+.24;r->d->qpos[3]=1;
 double legs[]={0,.8,-1.6,0,.8,-1.6,0,-.8,1.6,0,-.8,1.6};
 for(int i=0;i<12;i++){r->d->qpos[7+i]=legs[i];r->d->ctrl[i]=legs[i];}
 mj_forward(r->m,r->d);return r;
}
void sw_robot_close(void*p){Robot*r=p;if(r){mj_deleteData(r->d);mj_deleteModel(r->m);free(r);}}
int sw_robot_count(void*p){return ((Robot*)p)->count;}
int sw_robot_visual_count(void*p){return ((Robot*)p)->visual_count;}
void sw_robot_visual(void*p,float*out){Robot*r=p;for(int k=0;k<r->visual_count;k++){int id=r->visual[k];for(int j=0;j<3;j++)out[12*k+j]=r->d->geom_xpos[id*3+j];for(int j=0;j<9;j++)out[12*k+3+j]=r->d->geom_xmat[id*9+j];}}
void sw_robot_spheres(void*p,float*out){Robot*r=p;for(int k=0;k<r->count;k++){int id=r->feet[k];double velocity[6];mj_objectVelocity(r->m,r->d,mjOBJ_GEOM,id,velocity,0);for(int j=0;j<3;j++){out[k*12+j]=r->d->geom_xpos[id*3+j];out[k*12+4+j]=velocity[3+j];out[k*12+8+j]=velocity[j];}out[k*12+3]=r->m->geom_size[id*3];out[k*12+7]=out[k*12+11]=0;}}
int sw_robot_body_pose(void*p,const char*name,float*out){Robot*r=p;int id=mj_name2id(r->m,mjOBJ_BODY,name);if(id<0)return 0;for(int j=0;j<3;j++)out[j]=r->d->xpos[id*3+j];for(int j=0;j<9;j++)out[3+j]=r->d->xmat[id*9+j];return 1;}
void sw_robot_state(void*p,double*out){Robot*r=p;for(int i=0;i<19;i++)out[i]=r->d->qpos[i];for(int i=0;i<6;i++)out[19+i]=r->d->qvel[i];for(int i=0;i<12;i++)out[25+i]=r->d->qfrc_bias[6+i];out[37]=0;for(int i=0;i<r->m->nbody;i++)if(i<=17)out[37]+=r->m->body_mass[i];out[38]=r->d->time;for(int i=0;i<9;i++)out[39+i]=r->d->xmat[9+i];}
void sw_robot_jacobian(void*p,int foot,double*out){Robot*r=p;double*jp=calloc(r->m->nv*3,sizeof(double));double*jr=calloc(r->m->nv*3,sizeof(double));mj_jacBody(r->m,r->d,jp,jr,r->m->geom_bodyid[r->feet[foot]]);for(int i=0;i<3;i++)for(int j=0;j<12;j++)out[i*12+j]=jp[i*r->m->nv+6+j];free(jp);free(jr);}
int sw_robot_step(void*p,const double*ctrl,const float*reactions){Robot*r=p;mju_zero(r->d->qfrc_applied,r->m->nv);for(int k=0;k<r->count;k++){int id=r->feet[k];double force[3],torque[3];for(int j=0;j<3;j++){force[j]=reactions[k*6+j];torque[j]=reactions[k*6+3+j];}mj_applyFT(r->m,r->d,force,torque,r->d->geom_xpos+id*3,r->m->geom_bodyid[id],r->d->qfrc_applied);}for(int i=0;i<12;i++)r->d->ctrl[i]=ctrl[i];mj_step(r->m,r->d);for(int i=0;i<r->m->nq;i++)if(!isfinite(r->d->qpos[i]))return 0;return 1;}
void sw_robot_push(void*p){Robot*r=p;r->d->qvel[0]+=.1;}
