#include "dolly_visualization.hpp"

#include <cmath>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iostream>
#include <iterator>
#include <limits>
#include <stdexcept>
#include <vector>

namespace {
void require(bool ok,const char* message) {if(!ok)throw std::runtime_error(message);}
bool close(double a,double b) {return std::abs(a-b)<.001;}
void u32(std::vector<unsigned char>& out,std::uint32_t value) {
    for(unsigned i=0;i<4;++i)out.push_back(static_cast<unsigned char>(value>>(8*i)));
}
void number(std::vector<unsigned char>& out,double value) {
    std::uint64_t bits;std::memcpy(&bits,&value,8);
    for(unsigned i=0;i<8;++i)out.push_back(static_cast<unsigned char>(bits>>(8*i)));
}
void change_u32(std::vector<unsigned char>& data,std::size_t offset,std::uint32_t value) {
    for(unsigned i=0;i<4;++i)data.at(offset+i)=static_cast<unsigned char>(value>>(8*i));
}
void change_number(std::vector<unsigned char>& data,std::size_t offset,double value) {
    std::vector<unsigned char> encoded;number(encoded,value);
    for(unsigned i=0;i<8;++i)data.at(offset+i)=encoded[i];
}
std::vector<unsigned char> packet(unsigned cameras=3,unsigned samples=2048,unsigned markers=64,bool step=false) {
    std::vector<unsigned char> blob{'D','L','Y','P','A','T','H',0};
    u32(blob,1);u32(blob,cameras-1);u32(blob,7);u32(blob,0);
    number(blob,cameras-1);number(blob,0);number(blob,cameras-1);
    auto pose=[](unsigned index) {return dolly::CameraPose{100+double(index)*10,double(index)*2,0,0,0,0,2};};
    for(auto v:pose(0))number(blob,v);
    for(auto v:pose(cameras-1))number(blob,v);
    for(unsigned i=0;i+1<cameras;++i) {
        number(blob,i);number(blob,i+1);
        for(unsigned c=0;c<7;++c) {
            u32(blob,step?0:1);u32(blob,c>=3?1:0);
            number(blob,pose(i)[c]);number(blob,pose(i+1)[c]);number(blob,0);number(blob,0);
        }
    }
    std::vector<unsigned char> out{'D','L','Y','V','I','S','0','1'};
    u32(out,2);u32(out,1);u32(out,1);u32(out,cameras/2);u32(out,cameras);
    u32(out,std::uint32_t(blob.size()));u32(out,samples);u32(out,markers);
    out.resize(64);
    for(unsigned i=0;i<cameras;++i)number(out,i);
    out.insert(out.end(),blob.begin(),blob.end());return out;
}
dolly::VisualizationView view() {
    dolly::VisualizationView v;v.pose={0,0,0,0,0,0,2};
    v.horizontal_fov=90;v.width=1000;v.height=500;return v;
}
void test_projection() {
    auto v=view();dolly::VisualizationPoint p{},a{},b{};
    require(dolly::project_visualization_point(v,{100,0,0},p)&&close(p.x,500)&&close(p.y,250),"Center projection failed");
    require(dolly::project_visualization_point(v,{100,-50,25},p)&&close(p.x,750)&&close(p.y,125),"Source right/up axes failed");
    require(!dolly::project_visualization_point(v,{-100,0,0},p),"Behind-camera point projected");
    require(!dolly::project_visualization_point(v,{100,500,0},p),"Offscreen point projected");
    require(!dolly::project_visualization_line(v,{-100,20,0},{-10,-30,0},a,b),"Behind-camera line projected");
    require(dolly::project_visualization_line(v,{-100,-300,0},{100,0,0},a,b),"Crossing near/frustum segment lost");
    require(close(a.x,1000)&&close(b.x,500)&&close(a.y,250),"Near/frustum clipping generated streak");
    require(dolly::project_visualization_line(v,{100,1000,0},{100,-1000,0},a,b)&&close(a.x,0)&&close(b.x,1000),"Viewport clipping failed");
    require(dolly::project_visualization_line(v,{100,0,1000},{100,0,-1000},a,b)&&close(a.y,0)&&close(b.y,500),"Vertical clipping failed");
    require(dolly::project_visualization_line(v,{-1,0,0},{100,0,0},a,b)&&close(a.x,500)&&close(b.x,500),"Near-plane center crossing failed");
    v.pose[4]=90;
    require(dolly::project_visualization_point(v,{0,100,0},p)&&close(p.x,500),"Yaw projection failed");
    v=view();v.pose[3]=45;
    require(dolly::project_visualization_point(v,{100,0,-100},p)&&close(p.y,250),"Pitch sign incorrect");
    v=view();v.pose[5]=90;
    require(dolly::project_visualization_point(v,{100,0,25},p)&&close(p.x,375)&&close(p.y,250),"Roll projection failed");
    v=view();v.pose[6]=1;
    require(dolly::project_visualization_point(v,{100,-50,25},p)&&close(p.x,750)&&close(p.y,187.5),"Projection aspect incorrectly replaced by window aspect");
    v.horizontal_fov=std::numeric_limits<double>::quiet_NaN();
    require(!dolly::project_visualization_point(v,{100,0,0},p),"Invalid lens accepted");
}
void self_test() {
    test_projection();
    auto bytes=packet();std::string error;dolly::VisualizationPath path;
    require(path.load(bytes.data(),bytes.size(),error),"Valid viewer packet rejected");
    require(path.enabled()&&path.points().size()==65&&path.cameras().size()==3,"Viewer sampling budget failed");
    require(path.points().front()[0]==100&&path.points().back()[0]==120,"Viewer endpoints changed");
    require(path.points()[32][0]==110&&path.points()[16][0]==105,"Viewer sampling does not match authored timing");
    require(path.selected_camera()==1,"Selected camera changed");
    dolly::VisualizationGeometry geometry;
    require(dolly::project_visualization(path,view(),geometry)&&geometry.label_count==3,"Camera markers did not project");
    require(geometry.line_count<=dolly::kVisualizationMaxLines,"Drawing exceeded its budget");
    bool selected=false;for(std::size_t i=0;i<geometry.label_count;++i)selected|=geometry.labels[i].selected&&geometry.labels[i].camera_index==1;
    require(selected,"Selected marker missing");
    auto stepped=packet(3,2048,64,true);
    require(path.load(stepped.data(),stepped.size(),error),"Stepped path rejected");
    require(path.breaks()[32]&&path.breaks()[64],"Step position jumps drawn as continuous travel");
    auto large=packet(4096,4096,128);
    require(path.load(large.data(),large.size(),error)&&path.points().size()==4096&&path.cameras().size()<=128,"Maximum path budgets failed");
    selected=false;for(const auto& camera:path.cameras())selected|=camera.index==2048;
    require(selected,"Decimation removed selected camera");
    auto single=packet(1);
    require(path.load(single.data(),single.size(),error)&&path.points().size()==1,"Single-camera path rejected");
    require(dolly::project_visualization(path,view(),geometry)&&geometry.label_count==1,"Single-camera glyph missing");
    for(std::size_t offset:{std::size_t(8),std::size_t(12),std::size_t(16),std::size_t(20),std::size_t(24),std::size_t(28),std::size_t(32),std::size_t(36),std::size_t(40)}) {
        auto bad=bytes;change_u32(bad,offset,0xFFFFFFFF);
        require(!path.load(bad.data(),bad.size(),error)&&path.points().size()==1,"Invalid header accepted or previous path destroyed");
    }
    auto bad=bytes;change_number(bad,64+8,1.5);
    require(!path.load(bad.data(),bad.size(),error),"Unrelated camera timestamp accepted");
    bad=bytes;bad.push_back(0);
    require(!path.load(bad.data(),bad.size(),error),"Trailing data accepted");
    for(std::size_t size=0;size<bytes.size();++size)
        require(!path.load(bytes.data(),size,error),"Truncated packet accepted");
    bad=bytes;bad.resize(64);change_u32(bad,16,0);change_u32(bad,20,0);change_u32(bad,24,0);change_u32(bad,28,0);
    require(path.load(bad.data(),bad.size(),error)&&!path.enabled(),"Clear packet did not disable guides");
    geometry.line_count=123;
    require(!dolly::project_visualization(path,view(),geometry)&&geometry.line_count==0,"Disabled guides retained stale lines");
}
} // namespace

int main(int argc,char** argv) {
    try {
        if(argc==2) {
            std::ifstream input(argv[1],std::ios::binary);
            std::vector<unsigned char> bytes((std::istreambuf_iterator<char>(input)),{});
            dolly::VisualizationPath path;std::string error;
            if(!path.load(bytes.data(),bytes.size(),error))throw std::runtime_error(error);
            std::cout.precision(17);
            std::cout<<path.points().size()<<' '<<path.cameras().size()<<' '<<path.selected_camera()<<'\n';
            for(const auto& pose:path.points()){for(double value:pose)std::cout<<value<<' ';std::cout<<'\n';}
            return 0;
        }
        self_test();std::cout<<"Native visualization tests passed\n";return 0;
    }catch(const std::exception& error){std::cerr<<error.what()<<'\n';return 1;}
}
