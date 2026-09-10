#include "dolly_flight.hpp"
#include <stdexcept>
#include <cmath>
#include <iostream>
using namespace dolly;
static void require(bool condition){if(!condition)throw std::runtime_error("Native flight integration invariant failed");}
static bool near(double a,double b){return std::abs(a-b)<1e-7;}
int main(){
 CameraPose start={100,200,300,0,0,0,16.0/9};
 FlightInput input{};input.forward=1;
 CameraPose at60=start,at120=start;
 for(int i=0;i<60;++i)integrate_flight(at60,input,1.0/60,400,.08);
 for(int i=0;i<120;++i)integrate_flight(at120,input,1.0/120,400,.08);
 require(near(at60[0],500));for(int i=0;i<7;++i)require(near(at60[i],at120[i]));
 // World Z is up; right is -Y at yaw0, +X at yaw90.
 CameraPose right=start;input={};input.right=1;integrate_flight(right,input,.025,400,.08);require(near(right[1],190));
 right=start;right[4]=90;integrate_flight(right,input,.025,400,.08);require(near(right[0],110));
 CameraPose rise=start;input={};input.up=1;integrate_flight(rise,input,.025,400,.08);require(near(rise[2],310));
 // Diagonal travel has the same speed; mouse sensitivity is independent of dt.
 CameraPose diagonal=start;input.forward=1;integrate_flight(diagonal,input,.025,400,.08);
 double distance=std::hypot(diagonal[0]-100,diagonal[2]-300);require(near(distance,10));
 input={};input.mouse_x=10;input.mouse_y=20;
 CameraPose mouseA=start,mouseB=start;integrate_flight(mouseA,input,.01,400,.08);integrate_flight(mouseB,input,.02,400,.08);
 require(near(mouseA[3],1.6)&&near(mouseA[4],-.8));require(near(mouseA[3],mouseB[3])&&near(mouseA[4],mouseB[4]));
 CameraPose inverted=start;integrate_flight(inverted,input,.01,400,.08,true);require(near(inverted[3],-1.6));
 // A suspend/refocus backlog is discarded, and pitching cannot cross poles.
 CameraPose stalled=start;input.forward=1;integrate_flight(stalled,input,4,400,.08);require(stalled==start);
 input={};input.mouse_y=100000;integrate_flight(stalled,input,.01,400,.08);require(near(stalled[3],89.9));
 // Projection and authored bank are not silently changed by translation.
 require(near(at60[5],start[5])&&near(at60[6],start[6]));
 std::cout<<"Native flight integration tests passed.\n";
}
