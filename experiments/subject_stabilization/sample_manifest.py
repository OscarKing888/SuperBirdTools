"""Write the manual-ROI manifest for the six user-provided example JPEGs.

These are hand-selected search regions, NOT YOLO/SAM predictions.
Only the first image of each independent pair supplies the regions.
The photographs themselves are not distributed in this repository.
"""
from pathlib import Path
import json

POLYGONS = {
0: {
 'head_bill': [[(884,412),(922,410),(944,453),(922,504),(891,605),(870,617),(835,605),(839,574),(878,485)]],
 'torso': [[(938,487),(966,514),(1000,591),(1001,645),(973,661),(919,652),(885,610),(889,563)]],
 'left_leg': [[(902,659),(916,660),(922,744),(915,758),(903,758)]],
 'right_leg': [[(978,659),(993,659),(992,757),(978,757)]],
 'wings': [[(943,450),(981,443),(1028,478),(1058,521),(1054,615),(1070,654),(1010,630),(990,567),(960,523)]]},
2: {
 'head_bill': [[(865,417),(899,412),(922,451),(902,502),(864,602),(838,611),(814,599),(822,575),(855,490)]],
 'torso': [[(913,482),(948,510),(981,584),(1000,637),(967,665),(909,662),(871,617),(858,566)]],
 'left_leg': [[(882,670),(895,670),(901,747),(895,761),(882,758)]],
 'right_leg': [[(956,664),(972,664),(970,764),(953,764)]],
 'wings': [[(849,443),(868,451),(865,525),(842,545),(821,538),(817,505),(827,462)],[(926,458),(958,451),(1009,474),(1046,518),(1057,591),(1036,642),(993,640),(978,553)]]},
4: {
 'head_bill': [[(867,350),(909,349),(938,393),(918,425),(879,458),(819,534),(795,543),(779,531),(794,508),(847,424)]],
 'torso': [[(942,454),(982,477),(1037,544),(1077,635),(1057,665),(1008,653),(965,604),(936,554),(911,515)]],
 'left_leg': [[(995,649),(1010,645),(1030,724),(1023,751),(1020,779),(1004,779),(1009,737)]],
 'right_leg': [[(1045,666),(1062,666),(1067,780),(1052,780)]],
 'wings': [[(830,318),(860,337),(914,352),(982,392),(1035,447),(1000,481),(941,431),(876,380)],[(993,467),(1106,432),(1182,390),(1315,380),(1471,367),(1410,413),(1390,453),(1346,490),(1297,528),(1262,563),(1200,601),(1122,631),(1071,601),(1048,531)]]}
}
PAIRS = [('DSC09864.JPG','DSC09865.JPG'),('DSC09879.JPG','DSC09880.JPG'),('DSC09911.JPG','DSC09912.JPG')]


def build_manifest() -> dict:
    manifest = {'roi_provenance': 'Manual anatomical ROIs in first image of each pair; not YOLO or SAM outputs. Numeric motion is algorithm-estimated.', 'pairs': []}
    # Coordinates were selected on a 1488x992 display; normalize before analysis.
    for index, pair in zip([0,2,4], PAIRS):
        regions = {key: [[[x/1488,y/992] for x,y in polygon] for polygon in polygons]
                   for key,polygons in POLYGONS[index].items()}
        manifest['pairs'].append(dict(reference=pair[0], moving=pair[1],
                                     fit_regions=['left_leg','right_leg'], regions=regions))
    return manifest


if __name__ == '__main__':
    destination = Path(__file__).with_name('manifest.json')
    destination.write_text(json.dumps(build_manifest(),ensure_ascii=False,indent=2),encoding='utf-8')
    print(destination)
