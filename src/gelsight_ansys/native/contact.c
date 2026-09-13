/* Velocity-dependent friction and reversible short-range adhesion.
 * Double precision; all history lives in ANSYS-owned arrays.
 * Parameter layout: mus_x,muk_x,mus_y,muk_y,v0,tensile,gap_cutoff,cohesion,SLTO,FKT.
 */
#include <math.h>
#include <string.h>
#include <stdlib.h>
#ifdef _WIN32
#include <windows.h>
#define EXPORT __declspec(dllexport)
#else
#define EXPORT __attribute__((visibility("default")))
#endif
static double maximum(double a,double b) { return a>b?a:b; }
static double minimum(double a,double b) { return a<b?a:b; }
static double norm2(const double *a) { return hypot(a[0],a[1]); }
static void ellipse_projection(const double *trial,const double *limit,double *tau) {
    double norm=hypot(trial[0]/limit[0],trial[1]/limit[1]);
    if(norm<=1) { tau[0]=trial[0];tau[1]=trial[1];return; }
    if(fabs(limit[0]-limit[1])<1e-12*maximum(limit[0],limit[1])) {
        tau[0]=trial[0]/norm;tau[1]=trial[1]/norm;return;
    }
    double lo=0,hi=maximum(limit[0]*limit[0],limit[1]*limit[1])*maximum(1,norm-1);
    for(int it=0;it<60;it++) {
        double x=trial[0]/(1+hi/(limit[0]*limit[0]));
        double y=trial[1]/(1+hi/(limit[1]*limit[1]));
        if(hypot(x/limit[0],y/limit[1])<=1) break;
        hi*=2;
    }
    for(int it=0;it<45;it++) {
        double mid=(lo+hi)/2;
        double x=trial[0]/(1+mid/(limit[0]*limit[0]));
        double y=trial[1]/(1+mid/(limit[1]*limit[1]));
        if(hypot(x/limit[0],y/limit[1])>1) lo=mid; else hi=mid;
    }
    for(int i=0;i<2;i++) tau[i]=trial[i]/(1+(lo+hi)/2/(limit[i]*limit[i]));
}
static int friction(const double *p,const double *old,const double *increment,
                    double pressure,double attached,double kt,double timestep,
                    double *tau,double *plastic,double *coeff) {
    double trial[2]={old[0]+kt*increment[0],old[1]+kt*increment[1]};
    double coh=attached*p[7],limit[2]={p[0]*pressure+coh,p[2]*pressure+coh};
    if(pressure<=0 && coh<=0) {
        tau[0]=tau[1]=0;plastic[0]=plastic[1]=0;coeff[0]=p[0];coeff[1]=p[2];return 1;
    }
    for(int i=0;i<2;i++) limit[i]=maximum(limit[i],1e-30);
    if(hypot(trial[0]/limit[0],trial[1]/limit[1])<=1) {
        tau[0]=trial[0];tau[1]=trial[1];plastic[0]=plastic[1]=0;coeff[0]=p[0];coeff[1]=p[2];return 3;
    }
    /* Solve the slip-rate consistency equation on a bounded interval. A
     * silently truncated fixed-point iteration can violate the friction law. */
    double lo=0,hi=norm2(trial)/(kt*maximum(timestep,1e-30));
    for(int it=0;it<70;it++) {
        double speed=(lo+hi)/2;
        for(int i=0;i<2;i++) {
            coeff[i]=p[2*i+1]+(p[2*i]-p[2*i+1])*exp(-speed/p[4]);
            limit[i]=maximum(coeff[i]*pressure+coh,1e-30);
        }
        ellipse_projection(trial,limit,tau);
        for(int i=0;i<2;i++) plastic[i]=(trial[i]-tau[i])/kt;
        double updated=norm2(plastic)/maximum(timestep,1e-30);
        if(speed>updated) hi=speed;else lo=speed;
    }
    return 2;
}
EXPORT int gel_contact_response(const double *p,const double *old,const double *increment,
    double penetration,double multiplier,double kn,double kt,double timestep,
    double *stress,double *plastic,double *coefficient) {
    double pressure=maximum(0,multiplier+kn*penetration);
    double attached=p[5]>0 ? maximum(0,minimum(1,1+penetration/p[6])) : 0;
    stress[2]=pressure-p[5]*attached;
    return friction(p,old,increment,pressure,attached,kt,timestep,stress,plastic,coefficient);
}
/* Transform to an orthonormal tangent basis carried by the specimen fiber.
 * Q maps local slip coordinates to material tangent coordinates. */
EXPORT int gel_contact_oriented(const double *p,const double *old,const double *inc,
    double pen,double lambda,double kn,double timestep,const double *Q,
    double *stress,double *plastic,double *coeff) {
    double pressure=maximum(0,lambda+kn*pen);
    double attached=p[5]>0 ? maximum(0,minimum(1,1+pen/p[6])) : 0;
    double kt=p[9]*maximum(maximum(p[0],p[2])*pressure+attached*p[7],1e-3)/p[8];
    double a[2]={Q[0]*old[0]+Q[1]*old[1],Q[2]*old[0]+Q[3]*old[1]};
    double b[2]={Q[0]*inc[0]+Q[1]*inc[1],Q[2]*inc[0]+Q[3]*inc[1]};
    double t[3],v[2];
    int status=gel_contact_response(p,a,b,pen,lambda,kn,kt,timestep,t,v,coeff);
    stress[0]=Q[0]*t[0]+Q[2]*t[1];stress[1]=Q[1]*t[0]+Q[3]*t[1];stress[2]=t[2];
    plastic[0]=Q[0]*v[0]+Q[2]*v[1];plastic[1]=Q[1]*v[0]+Q[3]*v[1];
    return status;
}
#ifndef CONTACT_CORE_ONLY
#ifdef _WIN32
typedef int (*ElmGet)(int*,int*,int*);
typedef int (*Ndgall)(int*,double*);
typedef void (*Ndspgt)(int*,int*,int*,int*,double*,int*,double*);
static ElmGet get_element;static Ndgall get_node;static Ndspgt get_displacement;
static INIT_ONCE api_once=INIT_ONCE_STATIC_INIT;
static CRITICAL_SECTION api_lock;
static BOOL CALLBACK initialize_api(PINIT_ONCE once,PVOID parameter,PVOID *context) {
    (void)once;(void)parameter;(void)context;
    HMODULE host=GetModuleHandleA(NULL);
    get_element=(ElmGet)(void*)GetProcAddress(host,"ELMGET");
    get_node=(Ndgall)(void*)GetProcAddress(host,"NDGALL");
    get_displacement=(Ndspgt)(void*)GetProcAddress(host,"NDSPGT");
    InitializeCriticalSection(&api_lock);return TRUE;
}
static int specimen_basis(int target,const double *position,const double *localr,double *Q) {
    InitOnceExecuteOnce(&api_once,initialize_api,NULL,NULL);
    if(!get_element || !get_node || !get_displacement || target<=0) return 0;
    int attrs[256]={0},nodes[256]={0},ok=1;
    double x[4][3];
    EnterCriticalSection(&api_lock);
    int count=get_element(&target,attrs,nodes);
    if(abs(count)<4 || !nodes[0] || !nodes[1] || !nodes[2] || !nodes[3]) ok=0;
    if(ok) for(int i=0;i<4;i++) {
        double xyz[6]={0},u[3]={0};int dofs[2]={7,0},ndof=3,nrot=0,nvec=1;
        if(!get_node(nodes+i,xyz)) {ok=0;break;}
        get_displacement(nodes+i,dofs,&ndof,&nrot,xyz,&nvec,u);
        for(int j=0;j<3;j++) x[i][j]=xyz[j]+u[j];
    }
    LeaveCriticalSection(&api_lock);
    if(!ok) return 0;
    /* Our -z QUAD order is (x-,y-),(x-,y+),(x+,y+),(x+,y-).
     * Closest-point natural coordinates locate the local advected fiber. */
    double u=.5,v=.5,dx[3]={0},dy[3]={0};
    for(int it=0;it<8;it++) {
        double r[3],aa=0,bb=0,ab=0,ar=0,br=0;
        for(int j=0;j<3;j++) {
            dx[j]=(1-v)*(x[3][j]-x[0][j])+v*(x[2][j]-x[1][j]);
            dy[j]=(1-u)*(x[1][j]-x[0][j])+u*(x[2][j]-x[3][j]);
            r[j]=(1-u)*(1-v)*x[0][j]+(1-u)*v*x[1][j]+u*v*x[2][j]+u*(1-v)*x[3][j]-position[j];
            aa+=dx[j]*dx[j];bb+=dy[j]*dy[j];ab+=dx[j]*dy[j];ar+=dx[j]*r[j];br+=dy[j]*r[j];
        }
        double det=aa*bb-ab*ab;if(det<=1e-40) return 0;
        u=maximum(0,minimum(1,u-(bb*ar-ab*br)/det));
        v=maximum(0,minimum(1,v-(aa*br-ab*ar)/det));
    }
    double a=0,b=0;
    for(int j=0;j<3;j++) {
        dx[j]=(1-v)*(x[3][j]-x[0][j])+v*(x[2][j]-x[1][j]);
        a+=localr[3*j]*dx[j];b+=localr[1+3*j]*dx[j];
    }
    double length=hypot(a,b);if(length<=1e-20) return 0;
    Q[0]=a/length;Q[1]=b/length;Q[2]=-b/length;Q[3]=a/length;
    return 1;
}
#endif
EXPORT void USERINTER(int *ndim,double *coor,int *nkeyopt,int *keyopt,int *nrl,double *rlconst,
    int *npropu,double *uprop,int *nintin,int *intin,int *nrealin,double *realin,
    int *kupdhis,double *localr,int *nuval,int *nintp,double *usvr,int *ncomp,
    double *stress,double *strain0,double *strain,int *kstat,double *mu,double *dt,
    double *dtdp,int *kdamp,double *damp,double *fdiss,double *elener,int *keyerr,int *keycnv) {
    (void)coor;(void)nkeyopt;(void)keyopt;(void)nrl;(void)nintin;(void)nrealin;
    (void)strain0;
    if(*ndim!=3 || *ncomp!=9 || *npropu<10 || *nuval<24 || intin[1]<1 || intin[1]>*nintp) { *keyerr=1;return; }
    double *state=usvr+(intin[1]-1)*(*nuval);
    double kn=rlconst[2]<0 ? -rlconst[2] : realin[4]*rlconst[2];
    double ftol=rlconst[3]<0 ? -rlconst[3] : rlconst[3]*realin[1];
    if(kn<=0 || ftol<=0 || uprop[8]<=0) { *keyerr=1;return; }
    double penetration=strain[2],lambda=state[0];
    double pressure=maximum(0,lambda+kn*penetration);
    int needs_augmentation=(pressure>0 && fabs(penetration)>ftol) || penetration>ftol;
    /* Augment only after global equilibrium, at most once per Newton iteration.
     * These algorithmic multipliers are distinct from converged material history. */
    if(needs_augmentation) {
        *keycnv=0;
        if(intin[16]==1 && (state[1]!=intin[10] || state[2]!=intin[11] || state[3]!=intin[12])) {
            lambda=maximum(0,lambda+kn*penetration);state[0]=lambda;
            state[1]=intin[10];state[2]=intin[11];state[3]=intin[12];
        }
    }
    pressure=maximum(0,lambda+kn*penetration);
    double attached=uprop[5]>0 ? maximum(0,minimum(1,1+penetration/uprop[6])) : 0;
    double kt=uprop[9]*maximum(maximum(uprop[0],uprop[2])*pressure+attached*uprop[7],1e-3)/uprop[8];
    double Q[4]={1,0,0,1};
    if(uprop[0]!=uprop[2] || uprop[1]!=uprop[3]) {
#ifdef _WIN32
        if(!specimen_basis(intin[7],coor,localr,Q)) { *keyerr=1;return; }
#else
        *keyerr=1;return;
#endif
    }
    double old[2]={stress[0],stress[1]},increment[2]={strain[0],strain[1]},out[3],plastic[2],coeff[2];
    *kstat=gel_contact_oriented(uprop,old,increment,penetration,lambda,kn,realin[8],Q,out,plastic,coeff);
    memset(stress,0,9*sizeof(double));memcpy(stress,out,3*sizeof(double));
    memset(dt,0,81*sizeof(double));memset(dtdp,0,9*sizeof(double));
    memset(damp,0,9*sizeof(double));*kdamp=0;
    for(int j=0;j<3;j++) {
        double plus[3],minus[3],inc[2]={increment[0],increment[1]},pen=penetration,unused[2];
        double h=maximum(1e-12,maximum(uprop[8],fabs(strain[j]))*1e-5);
        if(j<2) inc[j]+=h;else pen+=h;
        gel_contact_oriented(uprop,old,inc,pen,lambda,kn,realin[8],Q,plus,unused,coeff);
        if(j<2) inc[j]-=2*h;else pen-=2*h;
        gel_contact_oriented(uprop,old,inc,pen,lambda,kn,realin[8],Q,minus,unused,coeff);
        for(int i=0;i<3;i++) dt[i+9*j]=(plus[i]-minus[i])/(2*h);
    }
    *mu=maximum(coeff[0],coeff[1]);
    *fdiss=maximum(0,stress[0]*plastic[0]+stress[1]*plastic[1]);
    *elener=(stress[0]*stress[0]+stress[1]*stress[1])/(2*kt);
    if(*kupdhis==1) {
        if(state[21]!=realin[7]) {
            state[4]+=norm2(plastic);state[12]+=plastic[0];state[13]+=plastic[1];state[14]+=*fdiss;
        }
        state[5]=pressure;state[6]=norm2(stress)/kt;
        state[7]=uprop[1]+(uprop[0]-uprop[1])*exp(-norm2(plastic)/maximum(realin[8],1e-30)/uprop[4]);
        state[8]=uprop[3]+(uprop[2]-uprop[3])*exp(-norm2(plastic)/maximum(realin[8],1e-30)/uprop[4]);
        state[9]=uprop[5]>0 ? maximum(0,minimum(1,1+penetration/uprop[6])) : 0;
        state[10]=stress[0];state[11]=stress[1];state[15]=realin[2];
        for(int i=0;i<3;i++) state[16+i]=coor[i];
        state[19]=coor[3];state[20]=coor[4];state[21]=realin[7];
        state[22]=*kstat;state[23]=stress[2];
    }
}
/* Append ANSYS-owned contact history as inspectable NMISC records. */
EXPORT void USEROU(int *elem,int *iout,int *nbsvr,double *bsvr,int *nnrsvr,double *nrsvr,
    int *npsvr,double *psvr,int *ncsvr,double *csvr,int *nusvr,double *usvr,int *nnode,
    int *nodes,double *xyz,double *vol,double *leng,double *time,double *timinc,
    int *nutot,double *utot,int *maxdat,int *numdat,double *udbdat) {
    (void)elem;(void)iout;(void)nbsvr;(void)bsvr;(void)nnrsvr;(void)nrsvr;
    (void)npsvr;(void)psvr;(void)ncsvr;(void)csvr;(void)nnode;(void)nodes;(void)xyz;
    (void)vol;(void)leng;(void)time;(void)timinc;(void)nutot;(void)utot;
    *numdat=0;
    if(*nusvr==96 && *maxdat>=96) { memcpy(udbdat,usvr,96*sizeof(double));*numdat=96; }
}
#endif
