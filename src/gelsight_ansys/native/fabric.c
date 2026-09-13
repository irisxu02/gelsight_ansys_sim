/* Provisional orthotropic fabric energy. SI units, thread-local temporaries.
 * W uses material-axis stretches and shear invariants of C = F^T F.
 * The z compression branch is the integral of the configured monotone curve.
 * Reference: docs/plane-material-adapters.md (added with validation results).
 */
#include <math.h>
#include <string.h>
#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT __attribute__((visibility("default")))
#endif
#define ND 7

typedef struct { double v, d[ND], h[ND*ND]; } Jet;
static Jet constant(double v) { Jet x; memset(&x,0,sizeof(x)); x.v=v; return x; }
static Jet variable(double v,int k) { Jet x=constant(v); x.d[k]=1; return x; }
static Jet add(Jet a,Jet b) {
    a.v+=b.v;
    for(int i=0;i<ND;i++) a.d[i]+=b.d[i];
    for(int i=0;i<ND*ND;i++) a.h[i]+=b.h[i];
    return a;
}
static Jet scale(Jet a,double b) {
    a.v*=b;
    for(int i=0;i<ND;i++) a.d[i]*=b;
    for(int i=0;i<ND*ND;i++) a.h[i]*=b;
    return a;
}
static Jet mul(Jet a,Jet b) {
    Jet x=constant(a.v*b.v);
    for(int i=0;i<ND;i++) x.d[i]=a.d[i]*b.v+a.v*b.d[i];
    for(int i=0;i<ND;i++) for(int j=0;j<ND;j++)
        x.h[i*ND+j]=a.h[i*ND+j]*b.v+a.d[i]*b.d[j]+a.d[j]*b.d[i]+a.v*b.h[i*ND+j];
    return x;
}
static Jet power(Jet a,double p) {
    Jet x=constant(pow(a.v,p));
    double d=p*pow(a.v,p-1), h=p*(p-1)*pow(a.v,p-2);
    for(int i=0;i<ND;i++) x.d[i]=d*a.d[i];
    for(int i=0;i<ND;i++) for(int j=0;j<ND;j++) x.h[i*ND+j]=d*a.h[i*ND+j]+h*a.d[i]*a.d[j];
    return x;
}
static Jet compression_energy(Jet compression,const double *p) {
    int n=(int)p[13];
    const double *e=p+14,*s=e+n,*m=s+n;
    double area=0;
    if(compression.v<0) return scale(mul(compression,compression),0.5*p[12]);
    if(compression.v>e[n-1]+1e-12) return constant(NAN);
    for(int k=0;k<n-1;k++) {
        double width=e[k+1]-e[k], slope=(s[k+1]-s[k])/width;
        double a=s[k],b=m[k],c=(3*slope-2*m[k]-m[k+1])/width;
        double d=(m[k]+m[k+1]-2*slope)/(width*width);
        if(compression.v<=e[k+1] || k==n-2) {
            Jet t=add(compression,constant(-e[k]));
            Jet value=add(constant(c/3),scale(t,d/4));
            value=add(constant(b/2),mul(t,value));
            value=add(constant(a),mul(t,value));
            return add(constant(area),mul(t,value));
        }
        area+=a*width+b*width*width/2+c*width*width*width/3+d*pow(width,4)/4;
    }
    return constant(NAN);
}
static Jet physical_energy(Jet *diagonal,Jet *shear_squared,const double *p) {
    Jet e[3],lambda[3],W=constant(0);
    for(int i=0;i<3;i++) { lambda[i]=power(diagonal[i],0.5);e[i]=add(lambda[i],constant(-1)); }
    for(int i=0;i<3;i++) for(int j=0;j<3;j++) W=add(W,scale(mul(e[i],e[j]),p[3*i+j]/2));
    const int left[3]={0,0,1},right[3]={1,2,2};
    for(int k=0;k<3;k++) {
        Jet denominator=mul(lambda[left[k]],lambda[right[k]]);
        W=add(W,scale(mul(shear_squared[k],power(denominator,-1)),p[9+k]/2));
    }
    W=add(W,compression_energy(scale(e[2],-1),p));
    return add(W,scale(mul(e[2],e[2]),-p[12]/2));
}
/* I4_i = Cbar_ii; I5_i = (Cbar^2)_ii. The three squared shear
 * components are recovered exactly from the three row-square invariants. */
static Jet energy(const double *values,const double *p) {
    Jet x[ND],diagonal[3],row[3],shear_squared[3];
    for(int i=0;i<ND;i++) x[i]=variable(values[i],i);
    Jet factor=power(x[6],2.0/3.0),factor2=mul(factor,factor);
    for(int i=0;i<3;i++) {
        diagonal[i]=mul(x[i],factor);
        row[i]=add(mul(x[i+3],factor2),scale(mul(diagonal[i],diagonal[i]),-1));
    }
    const int a[3]={0,0,1},b[3]={1,2,2},c[3]={2,1,0};
    for(int k=0;k<3;k++) shear_squared[k]=scale(add(add(row[a[k]],row[b[k]]),scale(row[c[k]],-1)),0.5);
    return physical_energy(diagonal,shear_squared,p);
}
/* Test interface uses full symmetric C = F^T F, independent of ANSYS. */
EXPORT double gel_fabric_energy(const double *C,const double *p,double *gradient,double *hessian) {
    Jet diagonal[3],shear_squared[3];
    for(int i=0;i<3;i++) {
        diagonal[i]=variable(C[i],i);
        Jet off=variable(C[3+i],3+i);shear_squared[i]=mul(off,off);
    }
    Jet W=physical_energy(diagonal,shear_squared,p);
    if(gradient) for(int i=0;i<6;i++) gradient[i]=W.d[i];
    if(hessian) for(int i=0;i<6;i++) for(int j=0;j<6;j++) hessian[i*6+j]=W.h[i*ND+j];
    return W.v;
}
#ifndef FABRIC_CORE_ONLY
/* Public ANSYS UPF utilities supply the invariant and packed-Hessian ordering. */
#include <windows.h>
typedef int (*IndexFunction)(int*,int*,int*,int*);
typedef void (*DerivativeFunction)(int*,int*,int*,int*,int*,int*,double*,double*,double*,double*);
static INIT_ONCE helpers_once=INIT_ONCE_STATIC_INIT;
static IndexFunction helper_index;
static DerivativeFunction helper_derivative;
static BOOL CALLBACK resolve_helpers(PINIT_ONCE once,PVOID parameter,PVOID *context) {
    (void)once;(void)parameter;(void)context;
    HMODULE solver=GetModuleHandleA(NULL);
    helper_index=(IndexFunction)GetProcAddress(solver,"FIINDX");
    helper_derivative=(DerivativeFunction)GetProcAddress(solver,"PUT_PDER");
    return TRUE;
}
EXPORT void USERHYPERANISO(int *settype,int *setnumber,int *incomp,int *upkey,
    int *nprop,double *prop,int *nfib,double *fibdir,int *ninv,double *invariants,
    double *potential,double *pd1,double *pd2,double *pd3) {
    (void)setnumber;(void)incomp;(void)fibdir;
    InitOnceExecuteOnce(&helpers_once,resolve_helpers,NULL,NULL);
    if(!helper_index || !helper_derivative) { *potential=NAN;return; }
    if(*nfib!=3 || *settype!=101 || *nprop<20 || *upkey!=0) { *potential=NAN; return; }
    int ids[ND],zero=0;
    double values[ND];
    for(int k=0;k<6;k++) {
        int i=k%3+1,j=i,kind=k<3 ? 1 : 2;
        ids[k]=helper_index(settype,&kind,&i,&j);values[k]=invariants[ids[k]-1];
    }
    ids[6]=3; values[6]=invariants[2];
    Jet W=energy(values,prop); *potential=W.v;
    memset(pd1,0,(size_t)*ninv*sizeof(double));
    memset(pd2,0,(size_t)(*ninv*(*ninv+1)/2)*sizeof(double));
    for(int i=0;i<ND;i++) {
        int order=1;
        helper_derivative(settype,nfib,&order,&ids[i],&zero,&zero,pd1,pd2,pd3,&W.d[i]);
        for(int j=i;j<ND;j++) {
            order=2;
            helper_derivative(settype,nfib,&order,&ids[i],&ids[j],&zero,pd1,pd2,pd3,&W.h[i*ND+j]);
        }
    }
}
#endif
